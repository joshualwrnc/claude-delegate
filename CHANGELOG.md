# Changelog

All notable changes to this skill are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-08-20

Hardening pass driven by an adversarial review of `SKILL.md` and two
behavioral evals run against an independent model.

### Added

- `compatibility:` frontmatter declaring that the skill needs a surface with a
  sub-agent tool. Advisory only — Claude Code accepts the field without acting
  on it — so README states the requirement too.
- `scripts/package_skill.py` — builds `dist/delegate.skill` for upload to a
  Claude account. Excludes `evals/` at the skill root, matching Anthropic's own
  packager, and produces byte-reproducible archives so CI can detect a stale
  artifact.
- `dist/delegate.skill` — prebuilt, uploadable package.
- `tests/test_repo.py` — 65 tests over the validator (every rule, positive and
  negative), the packager (single `SKILL.md`, excluded `evals/`, archive root,
  reproducibility, tampered-archive rejection), and `install.sh` end to end.
- Validator: `--for-upload` mode applying the stricter Skills API rules;
  checks for angle brackets in `description`, `compatibility` length, and
  exactly one *packaged* `SKILL.md`.
- Eval fixtures: a "never" rule present in two of three brand guides, so a run
  that skips a guide fails instead of guessing correctly.

### Changed — skill behavior

- **Step 0 triages before asking.** One small or tightly coupled task is done
  inline with no question. A stated cost preference ("do this cheaply") is now
  distinguished from a stated orchestrator choice.
- **Non-interactive sessions no longer block** on the orchestrator question —
  they keep the current model and note the choice. Previously "wait for the
  answer" had no fallback, which stalled scheduled and cloud runs.
- **Dependent waves run in the foreground.** "Never idle-poll" was being read
  as licence to start wave 2 early, contradicting the wave model and eval 3.
  It now means "don't re-check status in a loop".
- **Batch sizing has one rule** (5–10 items or one real seam per agent) plus a
  floor for tiny subtasks and a ~4–8 concurrent-agent ceiling. Two passages
  previously implied different shard sizes.
- **Dispatch prompts must cap the return** and state an expected item count.
  Uncapped returns land in the orchestrator's context at the orchestrator's
  rate, which was quietly cancelling the cost saving.
- **Coverage is accounted for before integrating.** A shard that returns
  nothing, errors, or comes back short is re-dispatched once and then reported
  as a stated gap. Partial coverage is never absorbed into a summary.
- **Long context goes by path, not by quotation**, and constraints must not be
  quoted unread. Added a rule against passing credentials or personal data
  into a dispatch prompt.
- **Review is an act, not a claim**: at least two adversarial spot-checks per
  deliverable, and a delegated verifier's verdict counts as evidence rather
  than as the review.
- **No fabricated cost figures.** Hardcoded per-token prices are gone — they
  included an intro rate expiring 2026-08-31 that would have made the skill's
  own "~1/5 the price" claim wrong within days. The final report now forbids
  percentage and dollar claims, since the orchestrator cannot see token counts.
- Infrastructure failures no longer count toward the two-strike escalation
  limit; a rejected `model` value is treated as a failed dispatch rather than
  silently running on the inherited model.
- The Sonnet-as-orchestrator finding is described as one data point, not a
  validation.

### Fixed

- README listed Cowork as a target surface; Anthropic's docs state Cowork "is
  not a Claude Code surface". Corrected, with VS Code marked unverified.

### Notes

- Eval cases 3 and 5 now pass at plan level. Case 4 (tier escalation) remains
  unverified: asserting it needs a sub-agent that genuinely fails review twice.

## [0.1.0] — 2026-08-20

Initial release.

### Added

- `delegate/SKILL.md` — orchestrator skill: confirm orchestrator, plan before
  spawning, route subtasks by tier, dispatch self-contained prompts, review
  before returning, and close with a cost/time accounting.
- `delegate/evals/evals.json` — 6 behavioral eval cases.
- `delegate/evals/fixtures/` — self-contained sample workspace so the eval
  suite is runnable from a clean clone.
- `scripts/validate_skill.py` — stdlib validator for frontmatter, eval
  structure, fixture-path existence, and leaked absolute local paths.
- `.github/workflows/validate.yml` — runs the validator on every push.
- `install.sh` — personal/project install and uninstall.
