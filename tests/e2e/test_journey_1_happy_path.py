"""Journey 1 happy path end-to-end (Story 3.11 AC #3 / FR15-21, FR30 code 0).

Seeds a real fixture cache via the in-process `steeproute-setup` CLI (committed
graphml, offline — see `conftest.seeded_cache`), then runs the `steeproute`
query CLI against it and asserts the documented happy-path contract: exit 0, one
`route-<i>.html` + one `route-<i>.json` per route for `i` in `1..N`, and each
HTML parses with the Leaflet map + Chart.js elevation-profile sections present.
"""

from __future__ import annotations

import html.parser
import json
import pathlib
from collections.abc import Callable

from click.testing import Result


def test_happy_path_writes_validated_reports_and_exits_0(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42)

    assert result.exit_code == 0, result.output

    html_files = sorted(output_dir.glob("route-*.html"))
    json_files = sorted(output_dir.glob("route-*.json"))
    n = len(html_files)
    assert n >= 1, "expected at least one route on the Grenoble fixture"
    assert len(json_files) == n, "one JSON sidecar per HTML report"

    # Filename pattern route-<i>.{html,json} for i in 1..N (FR21).
    for i in range(1, n + 1):
        html_path = output_dir / f"route-{i}.html"
        json_path = output_dir / f"route-{i}.json"
        assert html_path.exists(), f"missing {html_path.name}"
        assert json_path.exists(), f"missing {json_path.name}"

        html_text = html_path.read_text(encoding="utf-8")
        html.parser.HTMLParser().feed(html_text)  # parses without error
        assert 'id="map"' in html_text  # Leaflet map section (FR17)
        assert 'id="elevation-profile"' in html_text  # gradient profile (FR18)

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["route_index"] == i
        # Happy path: every route passed validation, so exit code was 0.
        assert payload["validation"]["passed"] is True
        assert payload["metadata"]["params"]["seed"] == 42  # FR29 seed recorded
