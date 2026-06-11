"""README ↔ gallery reference gate (Story 8.4 AC #4).

The `## Gallery` section of `README.md` surfaces the top route (`route-1.html`)
of each region committed under `docs/examples/`. When the gallery is regenerated
(regions renamed, added, or removed), the README links can silently drift out of
sync with what is actually on disk. This test pins that link: every surfaced
report must be referenced from the README.

Scope is the *surfaced* reports only — `docs/examples/<region>/route-1.html` — not
every committed HTML. Each region also commits `route-2/3.html` as supplementary
context reachable via the linked `docs/examples/` folder; the gallery deliberately
shows only route 1 (see docs/examples/README.md), so those are out of scope here.

Like the sibling self-containment gate, this runs offline against committed files,
is NOT `live`-marked (so the default `uv run pytest` CI enforces it), and fails
rather than passing vacuously if no surfaced reports are found.
"""

from __future__ import annotations

import pathlib

# Repo root, independent of the pytest invocation directory.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GALLERY_DIR = _REPO_ROOT / "docs" / "examples"
_README = _REPO_ROOT / "README.md"


def test_readme_references_every_surfaced_gallery_report() -> None:
    surfaced = sorted(_GALLERY_DIR.glob("*/route-1.html"))

    # Non-empty guard: zero surfaced reports means generation was skipped or the
    # path layout drifted - a regression, not a pass.
    assert surfaced, (
        f"no surfaced gallery reports (docs/examples/*/route-1.html) under "
        f"{_GALLERY_DIR} - the README ## Gallery links them; generate them per "
        f"docs/examples/README.md"
    )

    readme = _README.read_text(encoding="utf-8")

    missing = [
        rel
        for path in surfaced
        # README links use forward-slash repo-relative paths regardless of OS.
        if (rel := path.relative_to(_REPO_ROOT).as_posix()) not in readme
    ]

    assert not missing, (
        "README.md does not reference these surfaced gallery reports (gallery/"
        "README drift):\n  " + "\n  ".join(missing)
    )
