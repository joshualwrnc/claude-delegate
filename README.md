# delegate

A [Claude Code](https://code.claude.com) skill that turns the main session into an **orchestrator**: it plans, dispatches subtasks to the cheapest sub-agent model that is clearly up to each one, runs independent subtasks concurrently, and reviews the results before anything reaches you.

Two things it buys you:

- **Cost** — execution tokens bill at the sub-agent's rate instead of the session model's. Expensive tokens go to planning, judgment, and review; cheap tokens go to grep output and boilerplate.
- **Wall-clock time** — independent subtasks run in parallel waves rather than one after another.

It is also willing to tell you *not* to delegate. Spawning an agent costs a round trip and a context re-explanation; for one small step that overhead exceeds the work, and the skill says so instead of theatrically farming it out.

## Requires Claude Code

This skill drives the sub-agent (Agent/Task) tool with a per-agent `model` override, so it only works where that tool exists:

| Surface | Works? |
|---|---|
| Claude Code CLI | Yes |
| Claude Code on the web / cloud sessions | Yes |
| Desktop app, **Code** tab | Yes |
| JetBrains plugin (runs the CLI) | Yes |
| Claude Agent SDK | Yes, via `agents` / `AgentDefinition` — but `isolation` is not an SDK field |
| VS Code extension | Unverified — `/model` and worktrees are documented, sub-agent parity is not |
| **claude.ai chat** | **No** — no sub-agent tool to drive |
| Desktop app, **Cowork** tab | Unsupported — Anthropic's docs state Cowork "is not a Claude Code surface" |

In plain claude.ai chat the skill has nothing to orchestrate: it can still be *uploaded* (its frontmatter is spec-clean), it just cannot function. The `compatibility:` field in `SKILL.md` states this requirement in the [Agent Skills spec](https://code.claude.com/docs/en/skills) format, but note that Claude Code accepts that field **without acting on it** — it is documentation, not a guard, which is why the requirement is spelled out here too.

## Install

**As a `.skill` file — for your Claude account**

`dist/delegate.skill` is a prebuilt, ready-to-upload package. Download it and add it at claude.ai → Settings → Capabilities → Skills, or hand it to an org owner to provision organization-wide. Rebuild it any time with:

```bash
python3 scripts/package_skill.py     # -> dist/delegate.skill
```

This is also the route that reaches **cloud sessions**: a local `~/.claude/skills/` install does not exist in a fresh cloud container, so a skill enabled on your account is what syncs down (into `~/.claude/skills/synced/`) at session start.

**Personal — all your local projects**

```bash
git clone https://github.com/joshualwrnc/claude-delegate
cd claude-delegate
./install.sh
```

**Project — checked in, available to everyone who clones that repo**

```bash
./install.sh --project /path/to/your/repo
```

Note the visibility difference: a project install lives in that repo's `.claude/skills/`, so anyone with repo access gets the skill automatically. A personal install stays on your machine.

**Uninstall**

```bash
./install.sh --uninstall            # or --uninstall --project /path/to/repo
```

## Usage

Invoke it directly with `/delegate`, or just describe divisible work — the description triggers on "delegate", "use sub-agents", "farm this out", "do this cheaply", "split this up", "parallelize", and on large tasks that obviously divide.

The skill triages first: if the task is one small step or tightly coupled, it does the work inline and says why, without asking anything. Otherwise it confirms **who orchestrates**, since a model cannot switch itself:

1. **Current model** — best plan and review quality.
2. **Cheaper orchestrator** — you run `/model claude-sonnet-5` first.
3. **No delegation** — correct for small or tightly coupled tasks.

In a non-interactive session (a scheduled run, a cloud routine) it does not block on that question: it keeps the current model, notes the choice, and proceeds.

Then it writes a plan before spawning anything:

```
Delegation plan
- Wave 1 (parallel): audit pages 1–8 → haiku | audit pages 9–16 → haiku | audit pages 17–24 → haiku
  Each returns at most 15 lines: `page — claim — why stale`. Expected: 8 entries per agent.
- Wave 2 (after wave 1, consumes: the three finding lists): rank findings, pick worst 5 — inline (judgment)
- Wave 3 (parallel): rewrite each of the 5 pages → sonnet, worktree isolation
- Review: sonnet agent verifies each rewrite; orchestrator spot-checks and makes the final call
- Rationale: 24 of 29 subtask-units run on cheap tiers; audits run concurrently instead of serially
```

## How it routes work

| Tier | Subtask shape | Examples |
|---|---|---|
| `haiku` | Mechanical, pattern-following, easily verified | grep sweeps, file inventories, format conversion, schema extraction |
| `sonnet` | Routine skilled work with clear instructions | ordinary edits and refactors, tests from a spec, per-file audits against a checklist |
| `opus` | Genuinely hard reasoning, bounded scope | gnarly debugging, tricky algorithms, adversarial review |
| inline | Judgment-critical or context-dependent | architecture calls, unstated preferences, integrating results, talking to you |

The rule is *clearly* capable, not marginally capable — a failed cheap run plus a retry costs more than starting one tier up. Two failed reviews at a tier forces escalation or an inline fallback, and the escalation is reported rather than hidden.

Deliberately **not** in the skill: hardcoded prices. An earlier draft quoted per-token figures including an intro rate that expires 2026-08-31, which would have made the skill's own cost comparison wrong within days of being written. It now gives relative magnitudes and tells the orchestrator to check live pricing when a number matters.

Full details — dispatch prompt structure, return-size budgets, coverage accounting, the review protocol — are in [`delegate/SKILL.md`](delegate/SKILL.md).

## Testing

Two layers. Structural checks run in CI on every push; behavioral evals are run by hand against a real session.

```bash
python3 scripts/validate_skill.py              # structural
python3 scripts/validate_skill.py --for-upload # stricter: Skills API rules
python3 -m unittest discover -s tests -v       # 82 tests
```

`scripts/validate_skill.py` is stdlib-only. It verifies the frontmatter parses, `name` is kebab-case and matches its directory, `description` is within 1024 chars and free of angle brackets (the Skills API rejects those), `compatibility` is within 500 chars, and that the skill has exactly one *packaged* `SKILL.md`. `--for-upload` additionally rejects keys that Claude Code accepts locally but the Skills API does not, so a package can't be built that is guaranteed to fail on upload.

It also enforces two things about the eval suite that matter more than they sound: **every fixture path referenced by an eval must exist** (in its `prompt` and its `files` list), and **no absolute home path — `/home/<user>/`, `/Users/<user>/` — may appear in a tracked file**. An eval pointing at a missing file silently tests nothing, and a local absolute path makes the suite unrunnable for everyone else. Both of those were real defects in the first draft, caught by these checks. A file whose job is to *test* the leak scan opts out with a marker token.

`tests/test_repo.py` (82 tests) covers the validator (each rule, positive and negative), the packager (exactly one `SKILL.md`, `evals/` excluded, entries rooted at the skill folder, byte-reproducibility, rejection of tampered archives), and `install.sh` end to end against throwaway `HOME`s. CI additionally rebuilds `dist/delegate.skill` and fails if it differs from the committed copy — the archive is byte-reproducible, so any diff means the artifact is stale.

### Behavioral evals

`delegate/evals/evals.json` holds 6 cases in the format Anthropic's `skill-creator` eval loop expects. Prompts use repo-relative paths against the self-contained fixtures in `delegate/evals/fixtures/`, so run the harness from the repo root.

| # | Case | Asserts | Status |
|---|---|---|---|
| 0 | `divisible-brand-audit` | plans a parallel fan-out with explicit `model` params, then synthesizes | passed |
| 1 | `explicit-cheap-fanout` | skips the orchestrator question when already answered | passed |
| 2 | `too-small-to-delegate` | **declines** to delegate and explains why | passed |
| 3 | `dependent-waves` | wave 2 does not start before wave 1 returns | passed (plan-level) |
| 4 | `escalation-after-failed-review` | escalates a tier after two failures, never a third retry | **unverified** |
| 5 | `asks-orchestrator-question` | presents the Step-0 options when the user hasn't chosen | passed (plan-level) |

Cases 0–2 were run with-skill vs. no-skill baseline: 100% assertion pass vs. 72%.

Cases 3 and 5 were verified by an independent model reading only `SKILL.md` and responding to the prompts. "Plan-level" is the honest caveat: the test subject could not itself spawn sub-agents, so what was checked is the *plan* — that wave 2 was declared dependent and not dispatched early, and that the Step-0 question was asked before any work began. Both passed, and case 3's synthesis correctly contained only the two rules shared by all three fixtures.

Case 4 remains unverified because asserting it requires a sub-agent that genuinely fails review twice; a simulated failure would only test that the model can follow the escalation sentence it just read. Treat it as untested.

An earlier orchestrator-tier comparison found Sonnet 5 matching the top tier on plan quality and inline-vs-delegate judgment at roughly a fifth of the price, and Haiku following the plan format but mis-reporting its own execution — hence "not recommended as orchestrator". That was a single task, and the skill now describes it as one data point rather than a validation.

### Fixture design

The three brand guides share exactly two "never" rules, and a third rule appears in two of the three. That near-miss is deliberate: without it, the shared rules are identical enough that a run could read one guide, guess, and pass. With it, skipping a guide produces a confidently wrong answer instead of an obviously incomplete one. `tests/test_repo.py` asserts that ground truth, so the fixtures can't drift away from what the eval claims.

## Repo layout

```
delegate/
  SKILL.md                  the skill itself — this is the whole deliverable
  evals/
    evals.json              6 behavioral eval cases
    fixtures/               self-contained sample workspace the evals run against
scripts/
  validate_skill.py         structural + hygiene validator (CI entry point)
  package_skill.py          builds dist/delegate.skill, reproducibly
tests/
  test_repo.py              82 tests over validator, packager, installer
dist/
  delegate.skill            prebuilt, uploadable package
install.sh                  personal / project install and uninstall
.github/workflows/          validate, test, and check the artifact isn't stale
```

## License

[MIT](LICENSE).
