#!/usr/bin/env python3
"""Validate the delegate skill and its eval suite.

Runs with the standard library only, so CI needs no install step.

Checks:
  1. SKILL.md exists, has well-formed frontmatter, and a non-empty body.
  2. `name` is a valid skill slug and matches the containing directory.
  3. `description` is present and within Claude Code's length limit.
  4. evals.json parses and every eval carries the required fields.
  5. Every repo-relative path referenced in an eval prompt exists on disk.
  6. No absolute home-directory paths leaked into tracked text files.

Usage:
    python3 scripts/validate_skill.py            # validate ./delegate
    python3 scripts/validate_skill.py path/to/skill
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Claude Code truncates skill descriptions past this; keep well clear.
MAX_DESCRIPTION = 1024
MAX_NAME = 64

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Frontmatter keys Claude Code understands. Anything else is a warning, not an
# error, since the set grows over time.
KNOWN_KEYS = {
    "name",
    "description",
    "disable-model-invocation",
    "allowed-tools",
    "license",
    "version",
}

# Paths referenced from eval prompts, e.g. "delegate/evals/fixtures/brands/".
REPO_PATH_RE = re.compile(r"\b(?:delegate|scripts)/[A-Za-z0-9._/-]+")

# An absolute home path in a tracked file almost always means someone's local
# workspace leaked into the repo — which also makes the eval suite unrunnable
# for anyone else. Caught exactly that in this suite's first draft.
HOME_PATH_RE = re.compile(r"(?:/home/|/Users/)[A-Za-z0-9._-]+/")

TEXT_SUFFIXES = {".md", ".json", ".yml", ".yaml", ".py", ".sh", ".txt"}

errors: list[str] = []
warnings: list[str] = []


def error(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def parse_frontmatter(text: str, source: str) -> tuple[dict[str, str], str]:
    """Parse the leading `---` block as flat `key: value` pairs.

    Deliberately minimal: the skill format only uses single-line scalars, so
    this avoids a PyYAML dependency in CI. Anything more exotic is reported
    rather than silently mis-parsed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        error(f"{source}: must start with a '---' frontmatter delimiter")
        return {}, text

    try:
        close = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        error(f"{source}: frontmatter is never closed with '---'")
        return {}, text

    meta: dict[str, str] = {}
    for lineno, line in enumerate(lines[1:close], start=2):
        if not line.strip():
            continue
        if line.startswith((" ", "\t", "-")):
            error(f"{source}:{lineno}: nested/multi-line frontmatter is not supported")
            continue
        key, sep, value = line.partition(":")
        if not sep:
            error(f"{source}:{lineno}: expected 'key: value', got {line!r}")
            continue
        key = key.strip()
        if key in meta:
            error(f"{source}:{lineno}: duplicate frontmatter key {key!r}")
        meta[key] = value.strip()

    return meta, "\n".join(lines[close + 1 :])


def check_skill(skill_dir: Path) -> None:
    skill_md = skill_dir / "SKILL.md"
    rel = skill_md.relative_to(REPO_ROOT)
    if not skill_md.is_file():
        error(f"{rel}: missing")
        return

    meta, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"), str(rel))

    name = meta.get("name")
    if not name:
        error(f"{rel}: frontmatter is missing required key 'name'")
    else:
        if not SLUG_RE.match(name):
            error(f"{rel}: name {name!r} must be lowercase letters/digits separated by hyphens")
        if len(name) > MAX_NAME:
            error(f"{rel}: name is {len(name)} chars, limit is {MAX_NAME}")
        if name != skill_dir.name:
            error(f"{rel}: name {name!r} does not match directory {skill_dir.name!r}")

    description = meta.get("description")
    if not description:
        error(f"{rel}: frontmatter is missing required key 'description'")
    elif len(description) > MAX_DESCRIPTION:
        error(f"{rel}: description is {len(description)} chars, limit is {MAX_DESCRIPTION}")

    for key in meta:
        if key not in KNOWN_KEYS:
            warn(f"{rel}: unrecognized frontmatter key {key!r}")

    if not body.strip():
        error(f"{rel}: body is empty")
    elif not re.search(r"^#\s+\S", body, re.MULTILINE):
        warn(f"{rel}: body has no top-level '# ' heading")


def check_evals(skill_dir: Path) -> None:
    evals_path = skill_dir / "evals" / "evals.json"
    rel = evals_path.relative_to(REPO_ROOT)
    if not evals_path.is_file():
        warn(f"{rel}: no eval suite found")
        return

    try:
        data = json.loads(evals_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        error(f"{rel}: invalid JSON — {exc}")
        return

    if data.get("skill_name") != skill_dir.name:
        error(f"{rel}: skill_name {data.get('skill_name')!r} != directory {skill_dir.name!r}")

    cases = data.get("evals")
    if not isinstance(cases, list) or not cases:
        error(f"{rel}: 'evals' must be a non-empty list")
        return

    seen_ids: set[object] = set()
    seen_names: set[object] = set()
    for index, case in enumerate(cases):
        label = f"{rel}: eval[{index}]"
        if not isinstance(case, dict):
            error(f"{label}: expected an object")
            continue

        for field in ("id", "eval_name", "prompt", "expected_output"):
            if not case.get(field) and case.get(field) != 0:
                error(f"{label}: missing required field {field!r}")

        for field, seen in (("id", seen_ids), ("eval_name", seen_names)):
            value = case.get(field)
            if value in seen:
                error(f"{label}: duplicate {field} {value!r}")
            seen.add(value)

        # Every fixture a prompt points at must actually be in the repo,
        # otherwise the eval silently tests nothing.
        for match in REPO_PATH_RE.findall(str(case.get("prompt", ""))):
            candidate = match.rstrip(".,;:!?)")
            if not (REPO_ROOT / candidate).exists():
                error(f"{label}: prompt references missing path {candidate!r}")


def check_no_leaked_paths() -> None:
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if ".git" in path.parts:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue  # this file documents the pattern it looks for
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in sorted(set(HOME_PATH_RE.findall(text))):
            error(
                f"{path.relative_to(REPO_ROOT)}: absolute local path {match!r} — "
                "use a repo-relative path so the repo stays portable"
            )


def main() -> int:
    skill_dir = (REPO_ROOT / (sys.argv[1] if len(sys.argv) > 1 else "delegate")).resolve()
    if not skill_dir.is_dir():
        print(f"error: {skill_dir} is not a directory", file=sys.stderr)
        return 2

    check_skill(skill_dir)
    check_evals(skill_dir)
    check_no_leaked_paths()

    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}", file=sys.stderr)

    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1

    print(f"\nOK — {skill_dir.name} validated, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
