"""Unit tests for cli/_shared.run_entry_point exit-code wrapper."""

from collections.abc import Iterator

import pytest

from steeproute.cli._shared import run_entry_point, set_verbose
from steeproute.errors import PreExecutionError


@pytest.fixture(autouse=True)
def reset_verbose_flag() -> Iterator[None]:
    """Ensure the module-level _verbose state never leaks between tests."""
    set_verbose(False)
    yield
    set_verbose(False)


def _main_returns_zero() -> int:
    return 0


def _main_returns_one() -> int:
    return 1


def _main_raises_pre_execution_error() -> int:
    raise PreExecutionError("boom")


def _main_raises_pre_execution_error_with_detail() -> int:
    raise PreExecutionError("boom", detail="why it broke")


def _main_raises_keyboard_interrupt() -> int:
    raise KeyboardInterrupt


def test_run_entry_point_exits_zero_when_main_fn_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_entry_point(_main_returns_zero)
    assert exc_info.value.code == 0
    assert capsys.readouterr().err == ""


def test_run_entry_point_passes_through_int_return_for_validation_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_entry_point(_main_returns_one)
    assert exc_info.value.code == 1
    assert capsys.readouterr().err == ""


def test_run_entry_point_exits_two_on_pre_execution_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_entry_point(_main_raises_pre_execution_error)
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.err == "error: boom\n"
    assert captured.out == ""


def test_run_entry_point_exits_130_on_keyboard_interrupt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_entry_point(_main_raises_keyboard_interrupt)
    assert exc_info.value.code == 130
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_run_entry_point_omits_detail_when_verbose_is_false(
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_verbose(False)
    with pytest.raises(SystemExit) as exc_info:
        run_entry_point(_main_raises_pre_execution_error_with_detail)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert err == "error: boom\n"
    assert "why it broke" not in err


def test_run_entry_point_includes_detail_when_verbose_is_true(
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_verbose(True)
    with pytest.raises(SystemExit) as exc_info:
        run_entry_point(_main_raises_pre_execution_error_with_detail)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert err == "error: boom\n        why it broke\n"
