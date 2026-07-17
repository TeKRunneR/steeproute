# pyright: reportUnknownLambdaType=false, reportUnannotatedClassAttribute=false
# Reason: the stub lambdas use `*_a, **_k` (unannotatable) and `_FakeResponse` is a
# throwaway test double — same per-file relaxation the other app tests use for stubs.
"""Unit tests for `app.geocode` — the best-effort reverse-geocode seam (App Story 4.3).

The whole suite is offline (AGENTS.md), so every test stubs `geocode.urlopen`;
no test hits the real Nominatim. The seam's contract is that it NEVER raises and
degrades to `None` on any failure — these tests pin a success plus each failure
mode (network error, timeout, non-200, empty/bad body, no place, non-dict body).
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from steeproute.app import geocode


class _FakeResponse:
    """Minimal `urlopen` context-manager stand-in: a status + a JSON-encoded body."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Replace `geocode.urlopen` with `handler(request, timeout=...)`."""
    monkeypatch.setattr(geocode, "urlopen", handler)


def _json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def test_success_returns_most_specific_place(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _json_body(
        {"address": {"town": "Chamrousse", "county": "Isère", "state": "Auvergne"}}
    )
    _patch_urlopen(monkeypatch, lambda *_a, **_k: _FakeResponse(body))
    # `city`/`town`/`village`… order: `town` wins over the broader `county`/`state`.
    assert geocode.reverse_geocode(45.12, 5.88) == "Chamrousse"


def test_success_falls_back_to_display_name(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _json_body({"display_name": "Grenoble, Isère, France"})
    _patch_urlopen(monkeypatch, lambda *_a, **_k: _FakeResponse(body))
    # No `address` place fields, no `name` → first component of `display_name`.
    assert geocode.reverse_geocode(45.19, 5.72) == "Grenoble"


def test_network_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object) -> _FakeResponse:
        raise urllib.error.URLError("no route to host")

    _patch_urlopen(monkeypatch, _raise)
    assert geocode.reverse_geocode(45.0, 5.0) is None


def test_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object) -> _FakeResponse:
        raise TimeoutError("timed out")  # an OSError subclass

    _patch_urlopen(monkeypatch, _raise)
    assert geocode.reverse_geocode(45.0, 5.0) is None


def test_non_200_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, lambda *_a, **_k: _FakeResponse(b"{}", status=429))
    assert geocode.reverse_geocode(45.0, 5.0) is None


def test_unparseable_body_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, lambda *_a, **_k: _FakeResponse(b"not json <<<"))
    assert geocode.reverse_geocode(45.0, 5.0) is None


def test_no_place_in_response_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _json_body({"address": {"road": "D111", "postcode": "38410"}})
    _patch_urlopen(monkeypatch, lambda *_a, **_k: _FakeResponse(body))
    # An address with no populated-place field and no name/display_name → None.
    assert geocode.reverse_geocode(45.0, 5.0) is None


def test_non_dict_body_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, lambda *_a, **_k: _FakeResponse(_json_body({}) and b"[]"))
    assert geocode.reverse_geocode(45.0, 5.0) is None
