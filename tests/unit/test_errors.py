"""Unit tests for SteeprouteError exception hierarchy (Architecture §Category 10)."""

from steeproute.errors import (
    BadCLIArgError,
    CacheCorruptedError,
    CacheNotFoundError,
    DataSourceUnavailableError,
    PreExecutionError,
    SolverError,
    SteeprouteError,
)


def test_steeproute_error_is_subclass_of_exception() -> None:
    assert issubclass(SteeprouteError, Exception)


def test_steeproute_error_can_be_instantiated_directly() -> None:
    e = SteeprouteError("base message")
    assert isinstance(e, Exception)
    assert str(e) == "base message"


def test_pre_execution_error_round_trips_user_message_with_default_detail() -> None:
    e = PreExecutionError("something went wrong")
    assert e.user_message == "something went wrong"
    assert e.detail is None
    assert str(e) == "something went wrong"
    assert isinstance(e, SteeprouteError)


def test_pre_execution_error_round_trips_user_message_and_detail() -> None:
    e = PreExecutionError("something went wrong", detail="more context")
    assert e.user_message == "something went wrong"
    assert e.detail == "more context"
    assert str(e) == "something went wrong"


def test_pre_execution_error_accepts_keyword_user_message() -> None:
    e = PreExecutionError(user_message="boom", detail="why")
    assert e.user_message == "boom"
    assert e.detail == "why"


def test_bad_cli_arg_error_inherits_pre_execution_error() -> None:
    e = BadCLIArgError("bad flag", detail="--center=abc,def")
    assert e.user_message == "bad flag"
    assert e.detail == "--center=abc,def"
    assert isinstance(e, PreExecutionError)
    assert isinstance(e, SteeprouteError)
    assert isinstance(e, Exception)


def test_cache_not_found_error_inherits_pre_execution_error() -> None:
    e = CacheNotFoundError("no prepared area covers query")
    assert e.user_message == "no prepared area covers query"
    assert e.detail is None
    assert isinstance(e, PreExecutionError)
    assert isinstance(e, SteeprouteError)


def test_cache_corrupted_error_inherits_pre_execution_error() -> None:
    e = CacheCorruptedError("graph.pkl unreadable", detail="EOFError at byte 42")
    assert e.user_message == "graph.pkl unreadable"
    assert e.detail == "EOFError at byte 42"
    assert isinstance(e, PreExecutionError)
    assert isinstance(e, SteeprouteError)


def test_data_source_unavailable_error_inherits_pre_execution_error() -> None:
    e = DataSourceUnavailableError("Overpass API unreachable")
    assert e.user_message == "Overpass API unreachable"
    assert e.detail is None
    assert isinstance(e, PreExecutionError)
    assert isinstance(e, SteeprouteError)


def test_solver_error_inherits_pre_execution_error() -> None:
    e = SolverError("solver internal failure", detail="empty top-N after iter 5")
    assert e.user_message == "solver internal failure"
    assert e.detail == "empty top-N after iter 5"
    assert isinstance(e, PreExecutionError)
    assert isinstance(e, SteeprouteError)
