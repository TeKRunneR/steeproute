# Planning artifacts (BMAD)

Planning lives under `_bmad-output/planning-artifacts/`:
- `prd.md` is the authoritative source on scope/requirements/v1 in-out
  decisions (supersedes the brainstorming doc where they conflict).
- `epics.md` was slimmed 2026-07-04: completed epics' full detail was moved
  byte-for-byte into `archive/epics-completed-1-14.md` (originally
  `-1-12.md`; renamed as later completed epics are folded in — named so it
  doesn't match the `*epic*.md` glob other BMAD workflows scan). `epics.md`
  keeps only the Overview, Requirements Inventory, a one-line-per-epic summary
  table for completed epics (pointing at the archive), and full detail for
  active/future epics. When an epic finishes and is marked `done` in
  `sprint-status.yaml`, fold its section into the archive the same way (rename
  the archive file's upper bound to match) — batch this opportunistically,
  not per-story.
- BMAD story files (from `create-story`) should guide implementation, not
  pre-implement it in prose. Stick to `template.md`'s actual sections (Story /
  AC / Tasks / Dev Notes / References / Dev Agent Record) — don't invent
  sections like "Implementation sketches," "Anti-patterns," or "Verification
  commands." ACs belong at outcome altitude (~5-10 outcomes for a 4-bullet
  epic AC), not 15 micro-specs that pre-write the code. Target ~100-200 lines;
  Dev Notes should point at architecture/PRD sources, not duplicate them.
- Keep `sprint-status.yaml` lean: a `last_updated` date and a terse
  `# (was epic-N) <name>` tag per renumbered epic is enough. Narrative history
  goes in the correct-course proposal doc, not here.
- FR/requirements lists should stay at high altitude: exclude standard CLI
  hygiene (arg validation, `--quiet`, stderr/stdout separation), dev-only
  mechanisms (regression suites, quality benchmarking, CI checks), and
  project-deliverable artifacts (README contents, gallery examples) — those
  belong in quality-commitments/scope sections, not FRs. Merge FRs that
  describe one capability from multiple angles; don't split one capability
  into per-edge-case FRs.
