"""Gallery HTML self-containment gate (Story 8.3 AC #3).

The README `## Gallery` links pre-computed example reports committed under
`docs/examples/`. Each must be self-contained for the same reason every rendered
report is (Story 3.10 AC #3): no external resource-loading tags, so the file can
be opened from disk, emailed, or served from any static host without a network
round-trip for its own assets.

This reuses Story 3.10's grep verbatim (`tests/unit/test_output.py::
test_html_is_self_contained_no_external_resource_tags`): inline `<script>` /
`<style>` *bodies* may legitimately contain URL strings (the OpenTopoMap tile
template, Leaflet's attribution link), so the assertion targets resource-loading
tags only — not raw substrings.

The test runs offline against committed files and is intentionally NOT `live`-marked,
so the default `uv run pytest` invocation CI already executes enforces it. It also
guards against a vacuous pass: if `docs/examples/` holds no HTML at all (gallery
not generated, or a path-layout drift), the test FAILS rather than silently
collecting zero cases.
"""

from __future__ import annotations

import pathlib
import re

# Repo-root-relative so the gate is independent of the pytest invocation directory.
_GALLERY_DIR = pathlib.Path(__file__).resolve().parents[2] / "docs" / "examples"

# Resource-loading tags that would pull an external asset at open time. Mirrors
# tests/unit/test_output.py:341-352 exactly.
_EXTERNAL_SCRIPT = re.compile(r"<script[^>]*\bsrc\s*=")
_EXTERNAL_LINK = re.compile(r"<link\b")
_EXTERNAL_IMG = re.compile(r"<img[^>]*\bsrc\s*=\s*[\"']https?://")


def test_gallery_html_files_are_self_contained() -> None:
    html_files = sorted(_GALLERY_DIR.rglob("*.html"))

    # Non-empty guard: a gallery with zero committed reports is a regression
    # (generation skipped, or files landed outside docs/examples/), not a pass.
    assert html_files, (
        f"no gallery HTML found under {_GALLERY_DIR} - the README ## Gallery "
        f"references committed example reports; generate them per docs/examples/README.md"
    )

    offenders: list[str] = []
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(_GALLERY_DIR.parent.parent)
        if _EXTERNAL_SCRIPT.search(text):
            offenders.append(f"{rel}: external <script src=>")
        if _EXTERNAL_LINK.search(text):
            offenders.append(f"{rel}: external <link>")
        if _EXTERNAL_IMG.search(text):
            offenders.append(f"{rel}: external <img src=http(s)://>")

    assert not offenders, (
        "gallery reports must be self-contained (assets inlined, no external "
        "resource tags); offenders:\n  " + "\n  ".join(offenders)
    )
