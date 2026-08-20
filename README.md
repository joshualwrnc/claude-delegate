# delegate

A [Claude Code](https://code.claude.com) skill that turns the main session into an **orchestrator**: it plans, dispatches subtasks to the cheapest sub-agent model that is clearly up to each one, runs independent subtasks concurrently, and reviews the results before anything reaches you.

Two things it buys you:

- **Cost** — execution tokens bill at the sub-agent's rate instead of the session model's. Expensive tokens go to planning, judgment, and review; cheap tokens go to grep output and boilerplate.
- **Wall-clock time** — independent subtasks run in parallel waves rather than one after another.

It is also willing to tell you *not* to delegate. Spawning an agent costs a round trip and a context re-explanation; for one small step that overhead exceeds the work, and the skill says so instead of theatrically farming it out.

## Install

Pick whichever scope matches how you want it to follow you around.

**Personal — available in all your local projects**

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

**Your Claude account — needed for cloud, Cowork, and scheduled sessions**

Local `~/.claude/skills/` does not exist in a fresh cloud container, so a personal install is invisible to [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web) and to routines. To use it there, either enable the skill on your Claude account (it syncs down into `~/.claude/skills/synced/` at session start) or commit it to the target repo's `.claude/skills/`.

**Uninstall**

```bash
./install.sh --uninstall            # or --uninstall --project /path/to/repo
```

## Usage

Invoke it directly with `/delegate`, or just describe divisible work — the skill's description triggers on "delegate", "use sub-agents", "farm this out", "do this cheaply", "split this up", "parallelize", and on large tasks that obviously divide.

The run always starts by asking **who orchestrates**, because a model cannot switch itself:

1. **Current model** — best plan and review quality; overhead stays small because execution is delegated anyway.
2. **Cheaper orchestrator** — you run `/model claude-sonnet-5` first. Validated as adequate for routine delegation work.
3. **No delegation** — correct for small or tightly coupled tasks.

Then it writes a plan before spawning anything:

```
Delegation plan
- Wave 1 (parallel): audit pages 1–8 → haiku | audit pages 9–16 → haiku | audit pages 17–24 → haiku
- Wave 2 (after wave 1): rank findings + pick worst 5 — kept inline (judgment)
- Wave 3 (parallel): rewrite each of the 5 pages → sonnet, worktree isolation
- Review: sonnet agent verifies each rewrite; orchestrator makes the final call
- Rationale: ~90% of tokens land on haiku/sonnet rates; audits and rewrites run concurrently
```

## How it routes work

| Tier | Subtask shape | Examples |
|---|---|---|
| `haiku` | Mechanical, pattern-following, easily verified | grep sweeps, file inventories, format conversion, schema extraction |
| `sonnet` | Routine skilled work with clear instructions | ordinary edits and refactors, tests from a spec, per-file audits against a checklist |
| `opus` | Genuinely hard reasoning, bounded scope | gnarly debugging, tricky algorithms, adversarial review |
| inline | Judgment-critical or context-dependent | architecture calls, unstated preferences, integrating results, talking to you |

The rule is *clearly* capable, not marginally capable — a failed cheap run plus a retry costs more than starting one tier up. Two failed reviews at a tier forces escalation or an inline fallback, and the escalation is reported rather than hidden.

Full details, including dispatch-prompt structure and the review protocol, are in [`delegate/SKILL.md`](delegate/SKILL.md).

## Testing

`scripts/validate_skill.py` is the fast structural check — stdlib only, no install step, and it runs in CI on every push:

```bash
python3 scripts/validate_skill.py
```

It verifies the frontmatter parses, `name` matches the directory, `description` is within the length limit, `evals.json` is well-formed with unique ids, **every fixture path referenced by an eval actually exists**, and no absolute `/home/...` or `/Users/...` path has leaked into a tracked file. The last two matter more than they sound: an eval pointing at a missing file silently tests nothing, and a local absolute path makes the suite unrunnable for everyone else.

`delegate/evals/evals.json` holds the behavioral suite — 6 cases in the format Anthropic's `skill-creator` eval loop expects. Prompts use repo-relative paths against the self-contained fixtures in `delegate/evals/fixtures/`, so run the harness from the repo root.

The suite deliberately covers the failure modes, not just the happy path:

| # | Case | Asserts |
|---|---|---|
| 0 | `divisible-brand-audit` | plans a parallel fan-out with explicit `model` params, then synthesizes |
| 1 | `explicit-cheap-fanout` | skips the orchestrator question when already answered |
| 2 | `too-small-to-delegate` | **declines** to delegate and explains why |
| 3 | `dependent-waves` | wave 2 does not start before wave 1 returns |
| 4 | `escalation-after-failed-review` | escalates a tier after two failures, never a third retry |
| 5 | `asks-orchestrator-question` | presents the Step-0 options when the user hasn't chosen |

### Results so far

- 3 cases run with-skill vs. no-skill baseline (2026-08-20): **100% assertion pass vs. 72%**.
- Orchestrator-tier comparison on an identical task: Sonnet 5 matched Fable 5 on plan quality and inline-vs-delegate judgment at roughly 1/5 the price. Haiku followed the plan format but mis-reported its own execution — **not recommended as orchestrator**.
- Cases 3–5 (dependent waves, tier escalation, the orchestrator question) are written but **not yet run**. Treat those three as unverified.

## Repo layout

```
delegate/
  SKILL.md                  the skill itself — this is the whole deliverable
  evals/
    evals.json              6 behavioral eval cases
    fixtures/               self-contained sample workspace the evals run against
scripts/
  validate_skill.py         structural + hygiene validator (CI entry point)
install.sh                  personal / project install and uninstall
.github/workflows/          runs the validator on every push
```

## Caveats

The model prices quoted in `SKILL.md` are current as of 2026-08 and include a Sonnet intro rate that expires 2026-08-31. They inform routing decisions, so re-verify them before trusting a cost claim — the skill itself says to.

## License

[MIT](LICENSE).
