# Test Read Confidence Tags — Spec

Every test read carries exactly one confidence tag. The tag is a claim about
whether the observed difference would survive a rerun, not about whether the
result is good news.

## Inputs

- `n_a`, `n_b` — impressions per variant
- `c_a`, `c_b` — conversions per variant
- `days` — days the test ran
- `p` — two-proportion z-test p-value on conversion rate

## Tag ladder

| Tag | Requires |
|---|---|
| `high` | `p < 0.01`, both arms ≥ 5,000 impressions, both arms ≥ 50 conversions, `days ≥ 14` |
| `medium` | `p < 0.05`, both arms ≥ 2,000 impressions, both arms ≥ 25 conversions, `days ≥ 7` |
| `low` | `p < 0.10` and every `medium` floor met except one, missed by no more than 20% |
| `directional` | anything else with a point estimate |
| `none` | fewer than 10 conversions in either arm, or `days < 3` |

Evaluate top-down and take the first tag whose conditions are fully met.

## Mandatory downgrades

These apply *after* the ladder, and stack — two triggers means two steps down.

1. **Split-day boundary.** `days` not a multiple of 7 → down one step. Weekday
   and weekend traffic convert differently, so a partial week biases whichever
   arm caught more weekend.
2. **Late-start arm.** If the two arms did not start within 24h of each other,
   down one step, regardless of how long both ran.
3. **Single-day dominance.** If any one calendar day contributed more than 40%
   of either arm's conversions, down one step.
4. **Post-hoc segment.** If the read is on a segment that was not declared
   before the test started, cap at `directional` (not a step — a hard cap).
5. **Creative refresh mid-test.** Any asset swapped mid-flight → cap at `low`.

A downgrade can never move a tag below `none`, and `none` is never upgraded by
anything.

## Edge cases that trip people up

- A test with a huge `n` and `p < 0.001` still lands at `medium` if it ran 10
  days: the ladder's `high` row needs `days ≥ 14`, and 10 is not a multiple of 7
  so downgrade 1 applies as well. Ladder first, then downgrades.
- Downgrades 1 and 3 frequently co-occur (a partial week that included a
  promo spike). Both apply — that is two steps, not one.
- Downgrade 4 is a cap, not a step, so a post-hoc segment on an otherwise
  `high` read reports `directional`, not `medium`.
- Meeting a floor exactly (e.g. precisely 25 conversions) counts as met. The
  comparisons are inclusive.
- `low` allows exactly one missed `medium` floor. Two missed floors fall
  through to `directional` even if both were missed narrowly.
