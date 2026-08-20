---
name: delegate
description: Orchestrate work by delegating execution to cheaper sub-agent models matched to each subtask's difficulty, for cost/token efficiency and wall-clock speed. Use whenever the user says "delegate", "use sub-agents", "farm this out", "do this cheaply", "split this up", "parallelize", or hands over a large task that divides into independent pieces (multi-file audits, batch rewrites, research sweeps, repetitive edits across many targets). Also use when the user asks which model should do a piece of work, or wants the current session to plan and review while cheaper models execute. Do not use for small single-step tasks where spawning an agent costs more than doing the work inline.
---

# Delegate

Turn the main session into an **orchestrator**: it plans, dispatches subtasks to the cheapest sub-agent model that is still clearly up to each subtask, and reviews the results. Two wins are the point of this skill:

- **Cost/token efficiency** — execution tokens are billed at the sub-agent's rate, not the main session's. Fable-priced tokens should be spent on planning, judgment, and review, not on grep output and boilerplate.
- **Time efficiency** — independent subtasks run concurrently. A task that divides into N independent pieces should not run as N sequential pieces.

Delegation has overhead (spawn cost, context re-explanation, review). If the whole task is one small step, or intermediate results constantly change what happens next, do it inline and say why delegation wasn't worth it.

## Step 0 — Confirm the orchestrator

Before planning, confirm who orchestrates. The model cannot switch itself; the user runs `/model`. Present these options briefly (AskUserQuestion when available, otherwise plain text) and wait for the answer:

1. **Current model orchestrates (recommended when already on Opus/Fable)** — best plan quality and review judgment; orchestration overhead stays small because execution is delegated. Worth the price when decomposition itself is hard, review is high-stakes, or the plan has multi-wave dependencies.
2. **Cheaper orchestrator** — user runs `/model claude-sonnet-5` (or Opus) first. Sonnet 5 is a validated orchestrator for routine delegation work (batch audits, sweeps, rewrites): in head-to-head tests it produced plans and inline-vs-delegate judgments on par with Fable at ~1/5 the price. Avoid Haiku as orchestrator — it can follow the plan format but its self-reports and review judgment slip on messier work.
3. **No delegation** — do everything inline in this session. Right answer for small or tightly coupled tasks.

Skip the question only if the user already stated their choice or has a standing preference in this conversation.

## Step 1 — Plan first, always

Write the delegation plan before spawning anything, and show it to the user when the task is non-trivial:

1. **Decompose** the task into subtasks with a concrete deliverable each ("return the list of X", "edit file Y to do Z", not "help with the module").
2. **Classify** each subtask: difficulty (mechanical / routine / hard), independence (can it run without another subtask's output?), and verifiability (can the orchestrator cheaply check it's right?).
3. **Map to a model** using the matching table below. Verifiable + mechanical → cheapest tier; judgment-critical or hard-to-verify → stronger tier or keep inline.
4. **Group for concurrency**: subtasks with no dependency edge between them run in the same wave, spawned in a single message. Dependent subtasks form later waves.
5. **Size the batches**: an agent's spawn-and-context overhead should be a small fraction of its work. Don't spawn one agent per tiny item — batch small items (e.g., 10 small files → 2 agents × 5 files, not 10 agents). Conversely, don't pack a batch so large that one bad result forces re-running everything.
6. **Decide the review path** (Step 4): orchestrator reviews, or review is itself delegated.

Plan format to present:

```
Delegation plan
- Wave 1 (parallel): [subtask] → haiku | [subtask] → sonnet | ...
- Wave 2 (after wave 1): [subtask] → sonnet
- Kept inline: [subtask] — why
- Review: [orchestrator | delegated to <model>]
- Rough rationale: what this saves vs. doing it all in the main session
```

**Worked example** — "audit all 24 product pages for outdated claims, then fix the worst 5":

```
Delegation plan
- Wave 1 (parallel): audit pages 1–8 → haiku | audit pages 9–16 → haiku | audit pages 17–24 → haiku
  (batched 8 per agent; per-page agents would spend more on spawn overhead than auditing)
- Wave 2 (after wave 1): rank findings + pick worst 5 — kept inline (judgment)
- Wave 3 (parallel): rewrite each of the 5 pages → sonnet, worktree isolation (concurrent edits)
- Review: sonnet agent verifies each rewrite against the audit finding; orchestrator makes final accept call
- Rationale: ~90% of tokens land on haiku/sonnet rates; audits and rewrites run concurrently
```

## Step 2 — Match task to model

Pick the cheapest model that is *clearly* capable — not marginally capable. A failed cheap run plus a retry costs more than starting one tier up. Prices as of 2026-08 (per Mtok in/out): Haiku 4.5 ~$1/$5 · Sonnet 5 $2/$10 (intro; $3/$15 after 2026-08-31) · Opus 5 $5/$25 · Fable 5 $10/$50. Re-verify pricing if it matters to the recommendation.

| Tier (`model` param) | Delegate when the subtask is... | Examples |
|---|---|---|
| `haiku` | Mechanical, pattern-following, easily verified | Search/grep sweeps, file inventories, format conversion, applying a stated find-and-replace pattern, extracting data into a given schema, summarizing one document |
| `sonnet` | Routine skilled work with clear instructions | Ordinary coding/edits/refactors of known shape, writing tests from a spec, drafting copy from a brief, research with synthesis, per-file audits against a checklist |
| `opus` | Genuinely hard reasoning inside a bounded scope | Debugging a gnarly failure, a tricky algorithm, cross-cutting refactor design, adversarial review of complex changes |
| keep inline (orchestrator) | Judgment-critical, taste-dependent, or dependent on full conversation context | Final architecture calls, anything using the user's unstated preferences, integrating results, user communication |

Agent types compose with models: use `Explore` for read-only search fan-outs, `general-purpose` for multi-step execution, `Plan` for design subtasks. Pass `model` explicitly — otherwise the sub-agent inherits the expensive session model and the savings evaporate.

**Escalation rule:** if a sub-agent's output fails review twice on the same subtask, stop retrying at that tier — re-delegate one tier up or pull it inline. Note the escalation in the final report.

## Step 3 — Dispatch well

Sub-agents start with zero conversation context. Each prompt must be self-contained:

- **Context**: the minimum background needed (paths, constraints, relevant conventions — quote them, don't say "follow the repo conventions").
- **Scope**: exactly what to touch and what not to touch.
- **Deliverable**: the concrete output and format ("return raw data/diff/list, not a narrative").
- **Done-check**: how the agent should verify its own work before returning (run the linter, re-read the diff, count the items).

Spawn all same-wave agents in a single message so they actually run concurrently. Use `isolation: "worktree"` when multiple agents edit files in parallel in the same repo. Let agents run in the background and keep orchestrating; never idle-poll.

## Step 4 — Review

Review is the orchestrator's job by default: check each deliverable against its done-check, spot-check facts and edits, and integrate. But review itself can be delegated when that's cheaper or more objective:

- **Delegated verification** — a `sonnet` (or `haiku` for mechanical checks) agent verifies another agent's output against explicit criteria. Prompt it to *refute*, not confirm: "find what's wrong or missing with X, given criteria Y."
- **Independent review model** — for high-stakes output, delegate review to a model that didn't write it (e.g., Sonnet executes, Opus reviews). The orchestrator still makes the final accept/reject call and owns what goes back to the user.

Never forward sub-agent output to the user unreviewed. The orchestrator's final message must integrate results in its own words — the user never saw the sub-agent transcripts.

## Final report

Close with a short accounting so the user can judge whether delegation paid off: what was delegated to which tier, what ran concurrently, what was escalated or kept inline, and the qualitative cost/time win (or an honest note if delegation wasn't worth it this time).
