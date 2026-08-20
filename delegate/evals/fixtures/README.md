# Sample Workspace

This is a throwaway fixture workspace used by the `delegate` eval suite. Nothing
here describes a real product or company.

## Layout

- `brands/` — three brand guides with partly overlapping rules. Exactly two
  "never" rules appear in all three. A third appears in two of the three, so a
  run that skips a guide reaches a confidently wrong answer rather than an
  obviously incomplete one.
- `skills/` — four tiny skill folders, for fan-out evals
- `platforms/` — three short platform notes
- `confidence-tags.md` — a spec with deliberately subtle edge cases
