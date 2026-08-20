# Changelog

All notable changes to this skill are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] — 2026-08-20

Initial release.

### Added

- `delegate/SKILL.md` — orchestrator skill: confirm orchestrator, plan before
  spawning, route subtasks by tier, dispatch self-contained prompts, review
  before returning, and close with a cost/time accounting.
- `delegate/evals/evals.json` — 6 behavioral eval cases covering parallel
  fan-out, skipping the orchestrator question, declining to delegate trivial
  work, dependent waves, tier escalation after failed review, and asking the
  Step-0 question.
- `delegate/evals/fixtures/` — self-contained sample workspace so the eval
  suite is runnable from a clean clone.
- `scripts/validate_skill.py` — stdlib validator for frontmatter, eval
  structure, fixture-path existence, and leaked absolute local paths.
- `.github/workflows/validate.yml` — runs the validator on every push.
- `install.sh` — personal/project install and uninstall.

### Notes

- Eval cases 3–5 are written but not yet executed; their assertions are
  unverified.
- Sonnet 5 is validated as an adequate orchestrator for routine delegation
  work. Haiku is not recommended as orchestrator — it mis-reported its own
  execution during testing.
