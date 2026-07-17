"""Best-effort reverse geocoding for run labels (App Story 4.3, FR13).

A NEW outbound seam — an external HTTP call to a geocoder (Nominatim), NOT CLI
coupling — so it lives here, in its own module, and NOT under `cli_adapter/`,
which is strictly the CLI boundary (architecture-app.md §The load-bearing rule,
post-v1 note).

The contract is **best-effort / offline-safe**: `reverse_geocode` resolves a
`(lat, lon)` center to a nearby town/place name, and every failure mode — no
network, DNS/connection error, timeout, non-200, empty/unparseable body, no
place in the response — returns `None`. It NEVER raises to the caller, so the
job runner stays fully functional with no network at all: a failed lookup just
leaves the run without a label (the run library falls back to coordinates).

Conventions mirror the CLI's DEM downloader (`pipeline/dem_download.py`): stdlib
`urllib.request`, a descriptive `User-Agent` (required by Nominatim's usage
policy), and a modest env-overridable socket timeout. `reverse_geocode` is
synchronous/blocking; the API layer runs it off the event loop via
`asyncio.to_thread` so it never stalls the single worker or open SSE streams.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
from collections.abc import Callable
from typing import Any, cast
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# A geocoder is `(lat, lon) -> place name | None`. Injected via `create_app`
# (stored on `app.state.geocoder`) so tests use a no-network stub and production
# wires `reverse_geocode`; `None` on app.state disables labelling entirely.
GeocodeFn = Callable[[float, float], "str | None"]

# Nominatim's public endpoint. Reverse geocoding a coordinate → an address.
_NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"

# Nominatim's usage policy REQUIRES a descriptive User-Agent identifying the app;
# an anonymous request may be blocked. Mirrors `dem_download._USER_AGENT`.
_USER_AGENT = "steeproute/0.1 (web app reverse geocode)"


def _env_int(name: str, default: int) -> int:
    """Read an int tuning knob from the environment, falling back to `default`
    (missing or malformed → `default`). Same shape as `dem_download._env_int`."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("ignoring invalid %s=%r; using %d", name, raw, default)
        return default


# Kept short: the label is a nice-to-have looked up on the job-submission path,
# so a slow/unreachable geocoder must not hold up queuing. Override for slow links.
_HTTP_TIMEOUT_S: int = _env_int("STEEPROUTE_GEOCODE_TIMEOUT_S", 5)

# `zoom=10` asks Nominatim for city/town-level detail (not a full street address).
_ZOOM = 10

# Address fields in preference order — the most specific human-recognizable place
# first, widening to admin regions. `name`/`display_name` are last-ditch fallbacks.
_PLACE_FIELDS: tuple[str, ...] = (
    "city",
    "town",
    "village",
    "hamlet",
    "municipality",
    "suburb",
    "county",
    "state",
    "country",
)


def _pick_place_name(payload: dict[str, Any]) -> str | None:
    """Extract a single human place name from a Nominatim reverse response.

    Prefers the most specific populated-place field (`_PLACE_FIELDS`), then the
    result's own `name`, then the first component of `display_name`. Returns
    `None` if nothing usable is present."""
    address = payload.get("address")
    if isinstance(address, dict):
        address_map = cast("dict[str, Any]", address)
        for field in _PLACE_FIELDS:
            value = address_map.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    display = payload.get("display_name")
    if isinstance(display, str) and display.strip():
        # `display_name` is a comma-joined address; its first part is the place.
        return display.split(",", 1)[0].strip()
    return None


def reverse_geocode(lat: float, lon: float) -> str | None:
    """Best-effort nearby-place name for a `(lat, lon)` center, or `None`.

    NEVER raises: any network error, timeout, non-200, or unparseable/empty body
    is swallowed and returns `None` (offline-safe — see module docstring). Blocking
    (stdlib `urllib`); callers on the event loop must use `asyncio.to_thread`.
    """
    query = urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": _ZOOM, "addressdetails": 1}
    )
    request = Request(  # noqa: S310 — fixed https Nominatim URL, not user-controlled scheme
        f"{_NOMINATIM_REVERSE_URL}?{query}", headers={"User-Agent": _USER_AGENT}
    )
    try:
        with urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:  # noqa: S310 — see above
            if getattr(response, "status", 200) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        # URLError/OSError = network/timeout; ValueError/JSONDecodeError = bad body.
        # Best-effort: log at debug and degrade to no label, never propagate.
        logger.debug("reverse geocode failed for (%s, %s): %s", lat, lon, exc)
        return None
    if not isinstance(payload, dict):
        return None
    return _pick_place_name(cast("dict[str, Any]", payload))
