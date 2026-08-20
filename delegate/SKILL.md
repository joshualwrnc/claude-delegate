---
name: delegate
description: Orchestrate work by delegating execution to cheaper sub-agent models matched to each subtask's difficulty, for cost/token efficiency and wall-clock speed. Use whenever the user says "delegate", "use sub-agents", "farm this out", "do this cheaply", "split this up", "parallelize", or hands over a large task that divides into independent pieces (multi-file audits, batch rewrites, research sweeps, repetitive edits across many targets). Also use when the user asks which model should do a piece of work, or wants the current session to plan and review while cheaper models execute. Do not use for small single-step tasks where spawning an agent costs more than doing the work inline.
compatibility: Requires a surface that can spawn sub-agents with a per-agent model override - Claude Code CLI, web/cloud sessions, the desktop Code tab, JetBrains, or the Claude Agent SDK. Does not work in claude.ai chat, which has no sub-agent tool. Advisory only; nothing enforces this field.
---

# Delegate

Turn the main session into an **orchestrator**: it plans, dispatches subtasks to the cheapest sub-agent model that is clearly up to each subtask, and reviews the results. Two wins are the point:

- **Cost/token efficiency** — execution tokens are billed at the sub-agent's rate, not the main session's. Top-tier tokens should be spent on planning, judgment, and review, not on grep output and boilerplate.
- **Time efficiency** — independent subtasks run concurrently. A task that divides into N independent pieces should not run as N sequential pieces.

Delegation has overhead: spawn cost, context re-explanation, and review. **Review cost is part of the budget** — if reviewing a deliverable costs more orchestrator tokens than producing it would have, that subtask should have stayed inline. If the whole task is one small step, or intermediate results constantly change what happens next, do it inline and say why delegation wasn't worth it.

## Step 0 — Triage, then confirm the orchestrator

First judge in a sentence or two whether the task is divisible at all. If it is one small step, or tightly coupled end to end, do it inline, say why, and ask nothing.

Otherwise confirm who orchestrates. You cannot switch your own model; the user runs `/model`.

- **The user already named an orchestrator** ("current model orchestrates", "use sonnet") — skip the question.
- **The user expressed a cost or speed preference** ("do this cheaply", "quickly") — that is a goal, not an orchestrator choice. Still ask, but lead with the option their preference implies.
- **Non-interactive session** (scheduled run, cloud routine, no user turn available) — do not block. Keep the current model as orchestrator, note that choice in one line of the final report, and proceed.
- **Otherwise** ask, using AskUserQuestion when available, and wait. Do not present the plan in the same message — the plan depends on the answer.

The three options:

1. **Current model orchestrates (recommended when already on a top tier)** — best plan quality and review judgment; orchestration overhead stays small because execution is delegated. Worth it when decomposition is hard, review is high-stakes, or the plan has multi-wave dependencies.
2. **Cheaper orchestrator** — the user runs `/model claude-sonnet-5` first. In one head-to-head comparison on a routine batch task, Sonnet 5 produced a plan and inline-vs-delegate judgment on par with the top tier at a fraction of the cost. Treat that as a single data point, not a guarantee. Avoid Haiku as orchestrator — it can follow the plan format, but its self-reports and review judgment slip on messier work.
3. **No delegation** — do everything inline. Right for small or tightly coupled tasks.

If the user switches models, restate the task and the plan in your first message afterwards: the new session remembers nothing of this one.

## Step 1 — Plan first, always

Write the plan before spawning anything, and show it when the task is non-trivial:

1. **Decompose** into subtasks with a concrete deliverable each ("return the list of X", "edit file Y to do Z", not "help with the module").
2. **Classify** each: difficulty (mechanical / routine / hard), independence (can it run without another subtask's output?), verifiability (can you cheaply check it's right?).
3. **Map to a model** using the table below. Verifiable + mechanical → cheapest tier; judgment-critical or hard-to-verify → stronger tier or keep inline.
4. **Group for concurrency**: subtasks with no dependency edge run in the same wave, spawned in one message. Dependent subtasks form later waves, and the plan must name what each later wave consumes.
5. **Size the batches.** Default to 5–10 items, or one coherent seam (a file, a module, a brand), per agent. Prefer boundaries that follow real seams over arbitrary numeric splits, so a re-run needs no re-slicing. Two floors and a ceiling:
   - If a subtask is only a few minutes of reading for you, fold it into a sibling batch or do it inline. Three short files is one agent, not three.
   - Don't pack a batch so large that one bad result forces re-running the wave.
   - Keep a wave to roughly 4–8 concurrent agents. Beyond that they queue rather than parallelize, so grow the shards, not the fan-out.
6. **Decide the review path** (Step 4): orchestrator reviews, or review is itself delegated.

Plan format:

```
Delegation plan
- Wave 1 (parallel): [subtask] → haiku | [subtask] → sonnet | ...
- Wave 2 (after wave 1, consumes: [what]): [subtask] → sonnet
- Kept inline: [subtask] — why
- Review: [orchestrator | delegated to <model>]
- Rationale: the structural win vs. doing it all in the main session
```

**Worked example** — "audit all 24 product pages for outdated claims, then fix the worst 5":

```
Delegation plan
- Wave 1 (parallel): audit pages 1–8 → haiku | audit pages 9–16 → haiku | audit pages 17–24 → haiku
  (8 per agent; per-page agents would spend more on spawn overhead than auditing)
  Each returns at most 15 lines: `page — claim — why stale`. Expected: 8 entries per agent.
- Wave 2 (after wave 1, consumes: the three finding lists): rank findings, pick worst 5 — inline (judgment)
- Wave 3 (parallel): rewrite each of the 5 pages → sonnet, worktree isolation (concurrent edits)
- Review: sonnet agent verifies each rewrite against its audit finding; orchestrator spot-checks and makes the final call
- Rationale: 24 of 29 subtask-units run on cheap tiers; audits run concurrently instead of serially
```

## Step 2 — Match task to model

Pick the cheapest model that is *clearly* capable — not marginally capable. A failed cheap run plus a retry costs more than starting one tier up.

| Tier (`model` param) | Delegate when the subtask is... | Examples |
|---|---|---|
| `haiku` | Mechanical, pattern-following, easily verified | Search/grep sweeps, file inventories, format conversion, applying a stated find-and-replace pattern, extracting data into a given schema, summarizing one document |
| `sonnet` | Routine skilled work with clear instructions | Ordinary coding/edits/refactors of known shape, writing tests from a spec, drafting copy from a brief, research with synthesis, per-file audits against a checklist |
| `opus` | Genuinely hard reasoning inside a bounded scope | Debugging a gnarly failure, a tricky algorithm, cross-cutting refactor design, adversarial review of complex changes |
| keep inline (orchestrator) | Judgment-critical, taste-dependent, or dependent on full conversation context | Final architecture calls, anything using the user's unstated preferences, integrating results, user communication |

Each tier down is roughly 2–5× cheaper per token than the one above it, and the top tier to the cheapest spans roughly an order of magnitude. Prices change and intro rates expire, so don't quote figures from memory — check current pricing (or load a pricing reference skill) if a specific number matters to your recommendation. The top tier is an orchestrator, rarely a delegation target: if a subtask genuinely needs it, that is a signal to keep the work inline.

Agent types compose with models: `Explore` for read-only search fan-outs, `general-purpose` for multi-step execution, `Plan` for design subtasks. Pass `model` explicitly — otherwise the sub-agent inherits the expensive session model and the savings evaporate. If a `model` value or agent type is rejected, treat that dispatch as failed and fix the call; never proceed on an inherited model and call it delegation.

**Escalation rule:** if a sub-agent's output fails review twice on the same subtask, stop retrying at that tier — re-delegate one tier up or pull it inline, and note the escalation in the final report. Infrastructure failures (timeout, permission denial, missing path) do not count toward the two; wrong or low-quality output does.

## Step 3 — Dispatch well

Sub-agents start with zero conversation context. Each prompt must be self-contained:

- **Context**: quote short, decisive constraints verbatim — a naming rule, a forbidden pattern, an output schema. Never "follow the repo conventions". For anything longer than ~20 lines, give the agent the **path** and have it read the file itself; that keeps those bytes on the cheap tier instead of duplicating them across every prompt. Never quote a constraint you have not actually read.
- **Scope**: exactly what to touch and what not to touch.
- **Deliverable**: the concrete output, its shape, and a size budget — "at most 15 lines, one per finding, `file:line — claim — why`", "return only the unified diff", "a JSON array of N objects". For anything large, have the agent write a file and return the path plus a short summary; an uncapped return lands in your context at your rate and eats the saving.
- **Expected count**: state the exact number of items the agent owns ("all 8 pages, one entry each"), so a short return is detectable.
- **Done-check**: how the agent verifies its own work before returning (run the linter, re-read the diff, count the items).

**Sensitive data**: pass no credentials, tokens, keys, personal data, or privileged material into a dispatch prompt — name the source and let the agent read it under its own permissions. Repeat any standing exclusion (restricted domains, approved sources, do-not-read paths) in *every* prompt in the wave: a fan-out multiplies the places a rule can break while reducing the places you are watching.

Spawn all same-wave agents in one message so they actually run concurrently. Use `isolation: "worktree"` when multiple agents edit files in the same repo in parallel. A wave that a later wave depends on runs in the **foreground** — you wait for it. "Never idle-poll" means don't burn turns re-checking status; it does not mean start the next wave early. Background mode is for work nothing in flight depends on, and only when you have real work to do meanwhile.

## Step 4 — Review

**First, account for coverage.** Before integrating a wave, check each shard returned its expected count. Re-dispatch once, naming the gap, if a shard returns nothing, errors, or comes back short. If a shard still can't be completed, say so in the final answer as a stated gap ("pages 9–16 not audited"). Never absorb partial coverage into a confident summary.

Then review. It is the orchestrator's job by default: check each deliverable against its done-check, spot-check at least two facts or edits per deliverable chosen adversarially, and integrate. Review is an act, not a claim — name in the final report what you actually checked and what you rejected.

Review can be delegated when that's cheaper or more objective:

- **Delegated verification** — a `sonnet` (or `haiku` for mechanical checks) agent verifies another agent's output against explicit criteria. Prompt it to *refute*, not confirm: "find what's wrong or missing with X, given criteria Y."
- **Independent review model** — for high-stakes output, delegate review to a model that didn't write it (e.g. Sonnet executes, Opus reviews).

A delegated verifier's verdict is evidence, never the review itself: sample it yourself before accepting. Never forward sub-agent output to the user unreviewed — the final message must integrate results in your own words, because the user never saw the sub-agent transcripts.

## Final report

Close with a short accounting so the user can judge whether delegation paid off: which subtasks ran at which tier, what ran concurrently, what was escalated or kept inline, what you verified, and any coverage gap.

Report **structure, not measured savings**. You cannot see token counts, so never state a percentage or dollar figure. If a magnitude helps, derive it from countable work and label it an estimate ("23 of 26 files were read by haiku-tier agents"). If delegation didn't pay off this time, say so plainly.
