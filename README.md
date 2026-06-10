# steeproute

👉\[\[\[**This is the initial readme for your
[simple-modern-uv](https://github.com/jlevy/simple-modern-uv) template.** Fill it in and
delete this message!
Below are general setup instructions that you may remove or keep and adapt for your
project.\]\]\]

* * *

## Project Docs

For how to install uv and Python, see [installation.md](docs/installation.md).

For development workflows, see [development.md](docs/development.md).

For instructions on publishing to PyPI, see [publishing.md](docs/publishing.md).

* * *

## Development notes

### Pinned-regression goldens

Seeded GRASP is deterministic (FR29), so any change to a pinned fixture's output is a
behavior change worth noticing. `tests/e2e/test_pinned_regressions.py` runs `steeproute`
on each committed fixture cache (`tests/e2e/fixtures/<name>/cache/`) at an explicitly-pinned
param set + seed and compares a 5-field hash tuple per route (`objective`, `d_plus_m`,
`d_minus_m`, `edge_count`, `canonical_edge_sequence_hash`) against the committed golden in
`tests/e2e/goldens/<name>.json`. The match is **zero-tolerance**.

To intentionally update goldens after a justified behavior change:

```
uv run update-regression --all          # or: --fixture <name>
```

This re-runs the fixture(s), prints a before/after diff, and overwrites the golden file(s).

- **Any commit that updates a golden MUST state an explicit rationale in the commit message** —
  what behavior changed and why the new output is correct. Golden updates are never rubber-stamped.
- **Do not `pytest.skip` / `xfail` a pinned-regression test** to get a build green. If a gate must
  be disabled temporarily it requires an explicit issue reference and commit-message rationale
  (Architecture §Cat 11c).

* * *

*This project was built from
[simple-modern-uv](https://github.com/jlevy/simple-modern-uv).*
