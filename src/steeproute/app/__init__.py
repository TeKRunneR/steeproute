"""steeproute web App — a thin FastAPI + HTML/JS/Leaflet UI over the two CLIs.

See `_bmad-output/planning-artifacts/architecture-app.md` for the design. This
subpackage lives inside the `steeproute` distribution (not a separate package) so
the future `cli_adapter` can make read-only in-process imports of `steeproute.*`
while `setup`/`query` themselves still run only as subprocesses.
"""
