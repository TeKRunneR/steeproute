"""Unit tests for the single-worker queue + worker loop (App Story 1.3).

The worker is driven through real `asyncio.create_subprocess_exec` spawns of a
tiny fake CLI script (so the spawn → stdout-drain → exit-code path is genuinely
exercised), but with `build_argv` injected to point argv at the fake instead of
the real `steeproute-setup`. Scenarios run under `asyncio.run` (no
pytest-asyncio needed).

Covered: exit-0 → done (+ stdout tail), non-zero → failed (+ exit_code), a
poisoned job (argv build raises) failing without stalling the queue, a spawn
error (bad executable) → failed, and strict serial (concurrency=1) execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
import sys
import textwrap

from steeproute.app.models import AreaSpec, JobKind, JobRecord, JobStatus, utcnow_iso
from steeproute.app.queue import JobQueue, Worker
from steeproute.app.store import JobStore

# Fake CLI: prints a few stdout lines, optionally stamps a shared marker file with
# start/end (for the serial test), sleeps, and exits with a chosen code.
#   argv: <marker-or-"-"> <tag> <exit_code> <sleep_s>
_FAKE_CLI = textwrap.dedent(
    """
    import sys, time
    marker, tag, code, sleep_s = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    def stamp(text):
        if marker != "-":
            with open(marker, "a", encoding="utf-8") as f:
                f.write(text + "\\n")
    stamp(f"start {tag}")
    print(f"stage: fake-stage ... ({tag})")
    time.sleep(sleep_s)
    print(f"steeproute-setup: cache-miss ({tag})")
    stamp(f"end {tag}")
    sys.exit(code)
    """
).strip()


def _write_fake_cli(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "fake_cli.py"
    path.write_text(_FAKE_CLI, encoding="utf-8")
    return path


def _record(job_id: str) -> JobRecord:
    return JobRecord(
        id=job_id,
        kind=JobKind.SETUP,
        area=AreaSpec(center=(45.26, 5.788), radius_km=2.0),
        params={},
        status=JobStatus.QUEUED,
        created_at=utcnow_iso(),
    )


async def _await_terminal(store: JobStore, job_id: str, *, timeout: float = 10.0) -> JobRecord:
    """Poll the store until the job reaches a terminal state or the timeout hits."""
    deadline = asyncio.get_running_loop().time() + timeout
    terminal = {JobStatus.DONE, JobStatus.FAILED, JobStatus.STOPPED}
    while True:
        record = store.get(job_id)
        if record is not None and record.status in terminal:
            return record
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"job {job_id} did not terminate within {timeout}s")
        await asyncio.sleep(0.02)


async def _await_status(store: JobStore, job_id: str, want: JobStatus, *, timeout: float = 5.0) -> None:
    """Poll the store until the job reaches a specific status."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        record = store.get(job_id)
        if record is not None and record.status is want:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"job {job_id} did not reach {want} within {timeout}s")
        await asyncio.sleep(0.02)


def test_successful_job_reaches_done_with_stdout_tail(tmp_path: pathlib.Path) -> None:
    fake = _write_fake_cli(tmp_path)

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        record = _record("ok")
        store.create(record)
        queue.enqueue(record.id)

        def build_argv(_r: JobRecord) -> list[str]:
            return [sys.executable, str(fake), "-", "ok", "0", "0"]

        worker = Worker(store, queue, build_argv=build_argv)
        task = asyncio.create_task(worker.run())
        final = await _await_terminal(store, "ok")
        task.cancel()

        assert final.status is JobStatus.DONE
        assert final.exit_code == 0
        assert final.started_at is not None
        assert final.finished_at is not None
        # The scraped stdout tail is captured for diagnostics.
        assert any("cache-miss" in line for line in final.stdout_tail)

    asyncio.run(scenario())


def test_nonzero_exit_marks_failed_with_exit_code(tmp_path: pathlib.Path) -> None:
    fake = _write_fake_cli(tmp_path)

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        store.create(_record("bad"))
        queue.enqueue("bad")

        def build_argv(_r: JobRecord) -> list[str]:
            return [sys.executable, str(fake), "-", "bad", "2", "0"]

        worker = Worker(store, queue, build_argv=build_argv)
        task = asyncio.create_task(worker.run())
        final = await _await_terminal(store, "bad")
        task.cancel()

        assert final.status is JobStatus.FAILED
        assert final.exit_code == 2
        assert final.stdout_tail  # tail still captured on failure

    asyncio.run(scenario())


def test_poisoned_job_fails_without_stalling_queue(tmp_path: pathlib.Path) -> None:
    fake = _write_fake_cli(tmp_path)

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        store.create(_record("poison"))
        store.create(_record("healthy"))
        queue.enqueue("poison")
        queue.enqueue("healthy")

        def build_argv(record: JobRecord) -> list[str]:
            if record.id == "poison":
                raise RuntimeError("argv build blew up")
            return [sys.executable, str(fake), "-", "healthy", "0", "0"]

        worker = Worker(store, queue, build_argv=build_argv)
        task = asyncio.create_task(worker.run())
        poison = await _await_terminal(store, "poison")
        healthy = await _await_terminal(store, "healthy")
        task.cancel()

        assert poison.status is JobStatus.FAILED
        # The one bad job did not stop the next one from running to completion.
        assert healthy.status is JobStatus.DONE

    asyncio.run(scenario())


def test_spawn_error_marks_failed(tmp_path: pathlib.Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        store.create(_record("noexe"))
        queue.enqueue("noexe")

        def build_argv(_r: JobRecord) -> list[str]:
            return ["definitely-not-a-real-executable-xyz-42"]

        worker = Worker(store, queue, build_argv=build_argv)
        task = asyncio.create_task(worker.run())
        final = await _await_terminal(store, "noexe")
        task.cancel()

        assert final.status is JobStatus.FAILED
        assert final.failure_reason is not None
        assert "spawn-failed" in final.failure_reason

    asyncio.run(scenario())


def test_jobs_run_serially_no_overlap(tmp_path: pathlib.Path) -> None:
    fake = _write_fake_cli(tmp_path)
    marker = tmp_path / "markers.txt"

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        tags = ["a", "b", "c"]
        for tag in tags:
            store.create(_record(tag))
            queue.enqueue(tag)

        def build_argv(record: JobRecord) -> list[str]:
            return [sys.executable, str(fake), str(marker), record.id, "0", "0.1"]

        worker = Worker(store, queue, build_argv=build_argv)
        task = asyncio.create_task(worker.run())
        for tag in tags:
            await _await_terminal(store, tag)
        task.cancel()

    asyncio.run(scenario())

    # Concurrency = 1: each job's start is immediately followed by its own end,
    # and jobs run in submission order — never interleaved.
    events = marker.read_text(encoding="utf-8").splitlines()
    assert events == ["start a", "end a", "start b", "end b", "start c", "end c"]


def test_large_stderr_does_not_deadlock(tmp_path: pathlib.Path) -> None:
    # Regression: the child writes ~160 KB to stderr (newline-terminated, as a
    # real CLI does) *before* any stdout. If the worker drains only stdout, the
    # child blocks on a full stderr pipe buffer (~64 KB) and neither side ever
    # advances — a deadlock that hangs the queue. Draining both concurrently lets
    # it run to completion.
    script = (
        "import sys\n"
        "for i in range(4000):\n"
        "    sys.stderr.write('stderr line %d padding-padding-padding\\n' % i)\n"
        "sys.stderr.flush()\n"
        "print('done-line')\n"
        "sys.exit(0)\n"
    )

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        store.create(_record("floody"))
        queue.enqueue("floody")

        def build_argv(_r: JobRecord) -> list[str]:
            return [sys.executable, "-c", script]

        worker = Worker(store, queue, build_argv=build_argv)
        task = asyncio.create_task(worker.run())
        final = await _await_terminal(store, "floody")
        task.cancel()

        assert final.status is JobStatus.DONE
        assert final.stderr_tail  # stderr captured, not just drained
        assert any("done-line" in line for line in final.stdout_tail)

    asyncio.run(scenario())


def test_shutdown_interrupts_running_job_and_kills_child(tmp_path: pathlib.Path) -> None:
    # Regression: cancelling the worker (lifespan shutdown) mid-run must kill the
    # subprocess (no orphan) and record a terminal `interrupted` state — never
    # leave the record stuck at `running`.
    finished = tmp_path / "finished.txt"
    script = (
        "import sys, time\n"
        "print('started', flush=True)\n"
        "time.sleep(30)\n"
        f"open('{finished.as_posix()}', 'w').close()\n"  # reached only if NOT killed
        "sys.exit(0)\n"
    )

    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        store.create(_record("long"))
        queue.enqueue("long")

        def build_argv(_r: JobRecord) -> list[str]:
            return [sys.executable, "-c", script]

        worker = Worker(store, queue, build_argv=build_argv)
        task = asyncio.create_task(worker.run())
        await _await_status(store, "long", JobStatus.RUNNING)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        final = store.get("long")
        assert final is not None
        assert final.status is JobStatus.FAILED
        assert final.failure_reason == "interrupted"

    asyncio.run(scenario())

    # The killed child never reached its post-sleep marker write.
    assert not finished.exists()


_SLEEPER = "import sys, time\nprint('started', flush=True)\ntime.sleep(30)\nsys.exit(0)\n"


def test_stop_running_job_marks_stopped(tmp_path: pathlib.Path) -> None:
    # A hard cancel of the running job → `stopped`, exit 130 (CLI Ctrl-C
    # convention, not the OS kill code), no result.
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        store.create(_record("stopme"))
        queue.enqueue("stopme")

        def build_argv(_r: JobRecord) -> list[str]:
            return [sys.executable, "-c", _SLEEPER]

        worker = Worker(store, queue, build_argv=build_argv)
        task = asyncio.create_task(worker.run())
        await _await_status(store, "stopme", JobStatus.RUNNING)

        assert worker.stop("stopme") is True
        final = await _await_terminal(store, "stopme")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert final.status is JobStatus.STOPPED
        assert final.exit_code == 130
        assert final.result_dir is None

    asyncio.run(scenario())


def test_stop_during_spawn_window_is_honored(tmp_path: pathlib.Path) -> None:
    # Regression: a stop() that lands after the record flips to RUNNING but before
    # the child is exposed must still be honored (the pre-spawn window). The worker
    # tracks the active job id synchronously at RUNNING, records the intent, and
    # kills the child as soon as it exists — so the job ends `stopped`, not `done`.
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs")
        queue = JobQueue()
        store.create(_record("racer"))
        queue.enqueue("racer")

        def build_argv(_r: JobRecord) -> list[str]:
            return [sys.executable, "-c", _SLEEPER]

        async def spawn(argv: list[str]) -> asyncio.subprocess.Process:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            # A Stop arrives mid-spawn: the id is already tracked, but the proc is
            # not yet exposed — stop() must record the intent and return True.
            assert worker.stop("racer") is True
            return proc

        worker = Worker(store, queue, build_argv=build_argv, spawn=spawn)
        task = asyncio.create_task(worker.run())
        final = await _await_terminal(store, "racer")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert final.status is JobStatus.STOPPED
        assert final.exit_code == 130

    asyncio.run(scenario())
