"""Validated shared-memory representations for parallel solver state."""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from multiprocessing.shared_memory import SharedMemory
from typing import Final, cast

import numpy as np

from steeproute.models import Edge, Solution
from steeproute.solver.distinctness import CanonicalSet
from steeproute.solver.grasp import SolverStaticContext

__all__ = [
    "ArrayDescriptor",
    "SharedBlobDescriptor",
    "SharedBlobOwner",
    "SharedSolverDescriptor",
    "SharedSolverOwner",
    "SharedSolverState",
    "load_shared_blob",
]


_VERSION: Final = 1
_INT64 = np.dtype("<i8")
_FLOAT64 = np.dtype("<f8")


@dataclass(frozen=True, slots=True)
class SharedBlobDescriptor:
    version: int
    name: str
    size: int

    def validate(self) -> None:
        if self.version != _VERSION or not self.name or self.size < 1:
            raise ValueError("invalid shared-blob descriptor")


class SharedBlobOwner:
    def __init__(self, payload: bytes) -> None:
        if not payload:
            raise ValueError("shared blob cannot be empty")
        memory = SharedMemory(create=True, size=len(payload))
        self._memory: SharedMemory | None = memory
        try:
            assert memory.buf is not None
            memory.buf[: len(payload)] = payload
        except BaseException:
            self.close_unlink()
            raise
        self.descriptor: SharedBlobDescriptor = SharedBlobDescriptor(
            _VERSION, memory.name, len(payload)
        )

    def close_unlink(self) -> None:
        memory, self._memory = self._memory, None
        if memory is None:
            return
        try:
            memory.close()
        finally:
            try:
                memory.unlink()
            except FileNotFoundError:
                pass


def load_shared_blob(descriptor: SharedBlobDescriptor) -> object:
    descriptor.validate()
    memory = SharedMemory(name=descriptor.name, create=False, track=False)
    assert memory.buf is not None
    view = memory.buf[: descriptor.size]
    try:
        return pickle.loads(view)
    finally:
        view.release()
        memory.close()


@dataclass(frozen=True, slots=True)
class ArrayDescriptor:
    dtype: str
    shape: tuple[int, ...]
    offset: int
    nbytes: int


@dataclass(frozen=True, slots=True)
class SharedSolverDescriptor:
    version: int
    name: str
    size: int
    arrays: tuple[tuple[str, ArrayDescriptor], ...]
    sac_values: tuple[tuple[bool, tuple[str, ...]], ...]

    def validate(self) -> dict[str, ArrayDescriptor]:
        if self.version != _VERSION or not self.name or self.size < 1:
            raise ValueError("invalid shared-solver descriptor header")
        specs = dict(self.arrays)
        if len(specs) != len(self.arrays) or set(specs) != _ARRAY_NAMES:
            raise ValueError("invalid shared-solver array schema")
        occupied: list[tuple[int, int]] = []
        for name, spec in specs.items():
            expected_dtype = _EXPECTED_DTYPES[name]
            dtype = np.dtype(spec.dtype)
            if dtype != expected_dtype:
                raise ValueError("invalid shared-solver array dtype")
            expected = int(np.prod(spec.shape, dtype=np.int64)) * dtype.itemsize
            if (
                len(spec.shape) != 1
                or any(dimension < 0 for dimension in spec.shape)
                or spec.offset < 0
                or spec.nbytes != expected
            ):
                raise ValueError("invalid shared-solver array shape or size")
            if spec.offset % dtype.alignment or spec.offset + spec.nbytes > self.size:
                raise ValueError("invalid shared-solver array offset")
            occupied.append((spec.offset, spec.offset + spec.nbytes))
        occupied.sort()
        if any(
            right_start < left_end
            for (_, left_end), (right_start, _) in zip(occupied, occupied[1:], strict=False)
        ):
            raise ValueError("overlapping shared-solver arrays")
        return specs


_ARRAY_NAMES = {
    "node_ids",
    "start_nodes",
    "candidate_offsets",
    "candidate_u",
    "candidate_v",
    "candidate_v_dense",
    "candidate_key",
    "length_m",
    "d_plus_m",
    "d_minus_m",
    "avg_gradient",
    "sac_code",
    "blocking_offsets",
    "blocking_ids",
    "base_offsets",
    "base_ids",
    "lookup_order",
}
_EXPECTED_DTYPES = {
    name: _FLOAT64 if name in {"length_m", "d_plus_m", "d_minus_m", "avg_gradient"} else _INT64
    for name in _ARRAY_NAMES
}


def _checked_int64(values: list[int], label: str) -> np.ndarray:
    limit = np.iinfo(np.int64)
    if any(value < limit.min or value > limit.max for value in values):
        raise OverflowError(f"{label} contains a value outside signed int64")
    return np.asarray(values, dtype=_INT64)


class SharedSolverOwner:
    """Sole parent owner of one packed, immutable solver-state block."""

    def __init__(self, context: SolverStaticContext) -> None:
        arrays, sac_values = _flatten_context(context)
        specs: list[tuple[str, ArrayDescriptor]] = []
        offset = 0
        for name, array in arrays.items():
            alignment = array.dtype.alignment
            offset = (offset + alignment - 1) // alignment * alignment
            specs.append(
                (name, ArrayDescriptor(array.dtype.str, array.shape, offset, array.nbytes))
            )
            offset += array.nbytes
        memory = SharedMemory(create=True, size=max(offset, 1))
        self._memory: SharedMemory | None = memory
        try:
            assert memory.buf is not None
            for name, spec in specs:
                if spec.nbytes:
                    memory.buf[spec.offset : spec.offset + spec.nbytes] = arrays[name].tobytes()
        except BaseException:
            self.close_unlink()
            raise
        self.descriptor: SharedSolverDescriptor = SharedSolverDescriptor(
            _VERSION, memory.name, max(offset, 1), tuple(specs), sac_values
        )
        self.descriptor.validate()

    def close_unlink(self) -> None:
        memory, self._memory = self._memory, None
        if memory is None:
            return
        try:
            memory.close()
        finally:
            try:
                memory.unlink()
            except FileNotFoundError:
                pass


def _flatten_context(
    context: SolverStaticContext,
) -> tuple[dict[str, np.ndarray], tuple[tuple[bool, tuple[str, ...]], ...]]:
    node_ids = sorted(int(node) for node in context.graph.graph.nodes)
    dense = {node: index for index, node in enumerate(node_ids)}
    start_nodes = [dense[int(node)] for node in context.nodes]
    records = [record for node in node_ids for record in context.adjacency.get(node, ())]
    candidate_offsets = [0]
    count = 0
    for node in node_ids:
        count += len(context.adjacency.get(node, ()))
        candidate_offsets.append(count)

    def _sac_token(value: object) -> tuple[bool, tuple[str, ...]] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return False, (value,)
        if isinstance(value, list):
            items = cast(list[object], value)
            strings = [item for item in items if isinstance(item, str)]
            if len(strings) == len(items):
                return True, tuple(strings)
        raise TypeError(f"unsupported sac_scale value {value!r}")

    sac_values = tuple(
        sorted(
            {
                token
                for record in records
                if (token := _sac_token(record.edge.sac_scale)) is not None
            }
        )
    )
    sac_codes = {value: index for index, value in enumerate(sac_values)}
    base_codes: dict[tuple[int, int, int], int] = {}
    identities = sorted(
        {
            identity
            for record in records
            for identity in (
                context.segment_map.get(record.directed_id, frozenset({record.directed_id}))
                | record.blocking
            )
        }
    )
    base_codes.update((identity, index) for index, identity in enumerate(identities))

    blocking_offsets = [0]
    blocking_ids: list[int] = []
    base_offsets = [0]
    base_ids: list[int] = []
    for record in records:
        blocking_ids.extend(base_codes[value] for value in sorted(record.blocking))
        blocking_offsets.append(len(blocking_ids))
        full = context.segment_map.get(record.directed_id, frozenset({record.directed_id}))
        base_ids.extend(base_codes[value] for value in sorted(full))
        base_offsets.append(len(base_ids))

    lookup_order = sorted(range(len(records)), key=lambda index: records[index].directed_id)
    arrays = {
        "node_ids": _checked_int64(node_ids, "node ids"),
        "start_nodes": _checked_int64(start_nodes, "start-node indices"),
        "candidate_offsets": _checked_int64(candidate_offsets, "candidate offsets"),
        "candidate_u": _checked_int64([record.edge.node_u for record in records], "candidate u"),
        "candidate_v": _checked_int64([record.edge.node_v for record in records], "candidate v"),
        "candidate_v_dense": _checked_int64(
            [dense[record.edge.node_v] for record in records], "candidate destination indices"
        ),
        "candidate_key": _checked_int64([record.edge.key for record in records], "candidate keys"),
        "length_m": np.asarray([record.edge.length_m for record in records], dtype=_FLOAT64),
        "d_plus_m": np.asarray([record.edge.d_plus_m for record in records], dtype=_FLOAT64),
        "d_minus_m": np.asarray([record.edge.d_minus_m for record in records], dtype=_FLOAT64),
        "avg_gradient": np.asarray(
            [record.edge.avg_gradient for record in records], dtype=_FLOAT64
        ),
        "sac_code": _checked_int64(
            [
                -1 if (token := _sac_token(record.edge.sac_scale)) is None else sac_codes[token]
                for record in records
            ],
            "SAC codes",
        ),
        "blocking_offsets": _checked_int64(blocking_offsets, "blocking offsets"),
        "blocking_ids": _checked_int64(blocking_ids, "blocking ids"),
        "base_offsets": _checked_int64(base_offsets, "base offsets"),
        "base_ids": _checked_int64(base_ids, "base ids"),
        "lookup_order": _checked_int64(lookup_order, "lookup order"),
    }
    return arrays, sac_values


class SharedSolverState:
    """Attached read-only ndarray views whose handle owns their lifetime."""

    node_ids: np.ndarray
    start_nodes: np.ndarray
    candidate_offsets: np.ndarray
    candidate_u: np.ndarray
    candidate_v: np.ndarray
    candidate_v_dense: np.ndarray
    candidate_key: np.ndarray
    length_m: np.ndarray
    d_plus_m: np.ndarray
    d_minus_m: np.ndarray
    avg_gradient: np.ndarray
    sac_code: np.ndarray
    blocking_offsets: np.ndarray
    blocking_ids: np.ndarray
    base_offsets: np.ndarray
    base_ids: np.ndarray
    lookup_order: np.ndarray
    candidate_offsets_view: memoryview
    candidate_u_view: memoryview
    candidate_v_view: memoryview
    candidate_v_dense_view: memoryview
    candidate_key_view: memoryview
    length_m_view: memoryview
    d_plus_m_view: memoryview
    d_minus_m_view: memoryview
    avg_gradient_view: memoryview
    sac_code_view: memoryview
    blocking_offsets_view: memoryview
    blocking_ids_view: memoryview
    base_offsets_view: memoryview
    base_ids_view: memoryview
    lookup_order_view: memoryview

    def __init__(self, descriptor: SharedSolverDescriptor) -> None:
        specs = descriptor.validate()
        memory = SharedMemory(name=descriptor.name, create=False, track=False)
        if memory.size < descriptor.size:
            memory.close()
            raise ValueError("shared-solver block is smaller than its descriptor")
        self._memory: SharedMemory | None = memory
        self.sac_values: tuple[tuple[bool, tuple[str, ...]], ...] = descriptor.sac_values
        assert memory.buf is not None

        def _view(name: str) -> np.ndarray:
            spec = specs[name]
            array = np.ndarray(
                spec.shape, dtype=np.dtype(spec.dtype), buffer=memory.buf, offset=spec.offset
            )
            array.flags.writeable = False
            return array

        self.node_ids = _view("node_ids")
        self.start_nodes = _view("start_nodes")
        self.candidate_offsets = _view("candidate_offsets")
        self.candidate_u = _view("candidate_u")
        self.candidate_v = _view("candidate_v")
        self.candidate_v_dense = _view("candidate_v_dense")
        self.candidate_key = _view("candidate_key")
        self.length_m = _view("length_m")
        self.d_plus_m = _view("d_plus_m")
        self.d_minus_m = _view("d_minus_m")
        self.avg_gradient = _view("avg_gradient")
        self.sac_code = _view("sac_code")
        self.blocking_offsets = _view("blocking_offsets")
        self.blocking_ids = _view("blocking_ids")
        self.base_offsets = _view("base_offsets")
        self.base_ids = _view("base_ids")
        self.lookup_order = _view("lookup_order")
        self.candidate_offsets_view = memoryview(self.candidate_offsets)
        self.candidate_u_view = memoryview(self.candidate_u)
        self.candidate_v_view = memoryview(self.candidate_v)
        self.candidate_v_dense_view = memoryview(self.candidate_v_dense)
        self.candidate_key_view = memoryview(self.candidate_key)
        self.length_m_view = memoryview(self.length_m)
        self.d_plus_m_view = memoryview(self.d_plus_m)
        self.d_minus_m_view = memoryview(self.d_minus_m)
        self.avg_gradient_view = memoryview(self.avg_gradient)
        self.sac_code_view = memoryview(self.sac_code)
        self.blocking_offsets_view = memoryview(self.blocking_offsets)
        self.blocking_ids_view = memoryview(self.blocking_ids)
        self.base_offsets_view = memoryview(self.base_offsets)
        self.base_ids_view = memoryview(self.base_ids)
        self.lookup_order_view = memoryview(self.lookup_order)
        try:
            self._validate_contents()
        except BaseException:
            self.close()
            raise

    def _validate_contents(self) -> None:
        node_count = len(self.node_ids)
        candidate_count = len(self.candidate_u)
        candidate_columns = (
            self.candidate_v,
            self.candidate_v_dense,
            self.candidate_key,
            self.length_m,
            self.d_plus_m,
            self.d_minus_m,
            self.avg_gradient,
            self.sac_code,
        )
        if any(len(column) != candidate_count for column in candidate_columns):
            raise ValueError("shared-solver candidate columns have inconsistent lengths")
        if len(self.candidate_offsets) != node_count + 1:
            raise ValueError("shared-solver candidate offsets have invalid length")
        if (
            len(self.blocking_offsets) != candidate_count + 1
            or len(self.base_offsets) != candidate_count + 1
        ):
            raise ValueError("shared-solver segment offsets have invalid length")
        for offsets, values in (
            (self.candidate_offsets, self.candidate_u),
            (self.blocking_offsets, self.blocking_ids),
            (self.base_offsets, self.base_ids),
        ):
            if (
                int(offsets[0]) != 0
                or int(offsets[-1]) != len(values)
                or np.any(offsets[1:] < offsets[:-1])
            ):
                raise ValueError("shared-solver CSR offsets are invalid")
        if np.any(self.start_nodes < 0) or np.any(self.start_nodes >= node_count):
            raise ValueError("shared-solver start-node index is out of range")
        if np.any(self.candidate_v_dense < 0) or np.any(self.candidate_v_dense >= node_count):
            raise ValueError("shared-solver destination index is out of range")
        if np.any(self.sac_code < -1) or np.any(self.sac_code >= len(self.sac_values)):
            raise ValueError("shared-solver SAC code is out of range")
        if not np.array_equal(np.sort(self.lookup_order), np.arange(candidate_count)):
            raise ValueError("shared-solver lookup order is not a permutation")

    def close(self) -> None:
        memory, self._memory = self._memory, None
        if memory is not None:
            for name in _ARRAY_NAMES - {"node_ids", "start_nodes"}:
                view = getattr(self, f"{name}_view", None)
                if view is not None:
                    view.release()
            for name in _ARRAY_NAMES:
                delattr(self, name)
            memory.close()

    def edge(self, index: int) -> Edge:
        sac_code = self.sac_code_view[index]
        sac_scale: object = None
        if sac_code >= 0:
            is_list, values = self.sac_values[sac_code]
            sac_scale = list(values) if is_list else values[0]
        return Edge(
            node_u=self.candidate_u_view[index],
            node_v=self.candidate_v_view[index],
            key=self.candidate_key_view[index],
            length_m=self.length_m_view[index],
            d_plus_m=self.d_plus_m_view[index],
            d_minus_m=self.d_minus_m_view[index],
            avg_gradient=self.avg_gradient_view[index],
            sac_scale=sac_scale,  # pyright: ignore[reportArgumentType]
        )

    def directed_id(self, index: int) -> tuple[int, int, int]:
        return (
            self.candidate_u_view[index],
            self.candidate_v_view[index],
            self.candidate_key_view[index],
        )

    def canonical_ids(self, solution: Solution) -> CanonicalSet:
        result: set[int] = set()
        for edge in solution.edges:
            wanted = (edge.node_u, edge.node_v, edge.key)
            low, high = 0, len(self.lookup_order_view)
            while low < high:
                middle = (low + high) // 2
                row = self.lookup_order_view[middle]
                if self.directed_id(row) < wanted:
                    low = middle + 1
                else:
                    high = middle
            if low == len(self.lookup_order_view):
                raise ValueError(f"unknown directed edge identity {wanted}")
            row = self.lookup_order_view[low]
            if self.directed_id(row) != wanted:
                raise ValueError(f"unknown directed edge identity {wanted}")
            start, end = self.base_offsets_view[row], self.base_offsets_view[row + 1]
            result.update(self.base_ids_view[start:end])
        return frozenset(result)
