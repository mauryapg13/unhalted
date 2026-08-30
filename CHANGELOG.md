# Changelog

## [Unreleased]

### Added
- Gherkin specification split into executable feature files under `tests/features/`, wired to
  `pytest-bdd` and run in CI.
- Project skeleton: packaging, CI workflow, README, licence, breakage log.
- Walking skeleton of the pipeline: normalised `FailureSignal`, SQLite case store with an
  append-only audit table, the diagnosis taxonomy keyed on Razorpay's `error_reason`,
  `error_source` and `error_step`, NPCI execution windows and contact hours, the retry
  scheduler, and the control loop that takes one failure from signal to scheduled action.
- Workflow enforcement: a git pre-commit hook refusing commits on `main`, a Claude Code
  `PreToolUse` guard, and `CLAUDE.md` stating the working agreement.

### Fixed
- NPCI restricted execution windows corrected to both bands, `10:00-13:00` and `17:00-21:30` IST.
  The spec previously named only the first, which would have permitted an evening retry that NPCI
  forbids.
- Retry-after-alert interval corrected from 24 to 25 hours, matching Razorpay's documented
  initiation gap. The 24-hour figure is the RBI notification requirement; 25 hours is when the
  charge actually initiates.
