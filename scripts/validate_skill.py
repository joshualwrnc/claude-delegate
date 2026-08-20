#!/usr/bin/env python3
"""Validate the delegate skill and its eval suite.

Standard library only, so CI needs no install step.

Checks:
  1. SKILL.md exists, has well-formed frontmatter, and a non-empty body.
  2. Exactly one *packaged* SKILL.md (nested ones are rejected on upload).
  3. `name` is a valid kebab-case slug and matches the containing directory.
  4. `description` is present, within the length limit, and free of angle
     brackets (the Skills API rejects those).
  5. `compatibility`, if present, is within its length limit.
  6. Frontmatter keys are recognized; with --for-upload, unknown keys are
     errors rather than warnings.
  7. evals.json parses and every eval carries the required fields.
  8. Every repo-relative path referenced in an eval prompt exists on disk.
  9. No absolute home-directory paths leaked into tracked text files.

The frontmatter and packaging rules mirror Anthropic's own skill tooling so a
skill that passes here also passes on upload.

Usage:
    python3 scripts/validate_skill.py                  # validate ./delegate
    python3 scripts/validate_skill.py --for-upload     # stricter: upload rules
    python3 scripts/validate_skill.py path/to/skill
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_common import (  # noqa: E402
    REPO_ROOT,
    SKILL_FILENAME,
    build_parser,
    is_packaged,
    rel_to_repo,
    resolve_skill_dir,
)

MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
MAX_NAME = 64

SLUG_RE = re.compile(r"^[a-z0-9-]+$")

# Keys accepted by the Skills API / claude.ai on upload.
UPLOAD_ALLOWED_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
}
# Additionally understood by Claude Code when loading from the filesystem, but
# rejected on upload — so these are fine locally and fatal with --for-upload.
LOCAL_ONLY_KEYS = {"disable-model-invocation"}

# Repo-relative path references inside eval prompts. The leading lookbehind
# stops a match mid-path or mid-URL: without it, "github.com/user/claude-delegate/
# issues/5" yields the bogus candidate "delegate/issues/5", and "out/delegate/x.md"
# yields "delegate/x.md" — both failing CI for paths nobody claimed exist.
REPO_PATH_RE = re.compile(r"(?<![\w/.:-])(?:delegate|scripts|tests)/[A-Za-z0-9._/-]+")

# An absolute home path in a tracked file almost always means someone's local
# workspace leaked into the repo — which also makes the eval suite unrunnable
# for anyone else. Caught exactly that in this suite's first draft. No trailing
# slash is required: a bare "/home/<name>" at end of line is the same leak.
HOME_PATH_RE = re.compile(r"(?:/home/|/Users/)[A-Za-z0-9._-]+")

# A file containing this token is skipped by the leaked-path scan. Only for
# files whose job is to *test* the scan. Split so that this file does not
# contain the contiguous token and therefore does not exempt itself.
LEAK_EXEMPT_TOKEN = "LEAK-CHECK-" "EXEMPT"  # noqa: S105,ISC001 - not a credential

TEXT_SUFFIXES = {".md", ".json", ".yml", ".yaml", ".py", ".sh", ".txt"}


class Report:
    """Collects findings for one validation run.

    Instance state rather than module globals, so tests and the packager can
    call validate() repeatedly without findings leaking between runs.
    """

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self, stream_out=None, stream_err=None) -> None:
        # Resolved at call time, not as default arguments: Python binds
        # defaults once at definition, which would capture the original
        # streams and silently ignore any later redirect.
        stream_out = sys.stdout if stream_out is None else stream_out
        stream_err = sys.stderr if stream_err is None else stream_err
        for message in self.warnings:
            print(f"warning: {message}", file=stream_out)
        for message in self.errors:
            print(f"error: {message}", file=stream_err)


def parse_frontmatter(text: str, source: str, report: Report) -> tuple[dict[str, str], str]:
    """Parse the leading `---` block as flat `key: value` pairs.

    Deliberately minimal: the skill format only uses single-line scalars, so
    this avoids a PyYAML dependency in CI. Anything more exotic is reported
    rather than silently mis-parsed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        report.error(f"{source}: must start with a '---' frontmatter delimiter")
        return {}, text

    close = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close = index
            break
    if close is None:
        report.error(f"{source}: frontmatter is never closed with '---'")
        return {}, text

    meta: dict[str, str] = {}
    for lineno, line in enumerate(lines[1:close], start=2):
        if not line.strip():
            continue
        if line.startswith((" ", "\t", "-")):
            report.error(f"{source}:{lineno}: nested/multi-line frontmatter is not supported")
            continue
        key, sep, value = line.partition(":")
        if not sep:
            report.error(f"{source}:{lineno}: expected 'key: value', got {line!r}")
            continue
        key = key.strip()
        if key in meta:
            report.error(f"{source}:{lineno}: duplicate frontmatter key {key!r}")
        meta[key] = value.strip()

    return meta, "\n".join(lines[close + 1 :])


def check_skill(skill_dir: Path, report: Report, *, for_upload: bool) -> None:
    skill_md = skill_dir / SKILL_FILENAME
    rel = rel_to_repo(skill_md)
    if not skill_md.is_file():
        report.error(f"{rel}: missing")
        return

    # Upload accepts exactly one SKILL.md per skill; Claude Code's filesystem
    # would happily load nested ones, so this only bites at distribution time.
    packaged = [
        path
        for path in skill_dir.rglob(SKILL_FILENAME)
        if is_packaged(path.relative_to(skill_dir))
    ]
    if len(packaged) > 1:
        extras = sorted(
            str(p.relative_to(skill_dir)) for p in packaged if p.resolve() != skill_md.resolve()
        )
        report.error(
            f"{rel}: found {len(packaged)} packaged {SKILL_FILENAME} files, but a skill must "
            f"contain exactly one at <folder>/{SKILL_FILENAME} — extra: {', '.join(extras)}"
        )

    meta, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"), str(rel), report)

    name = meta.get("name")
    if not name:
        report.error(f"{rel}: frontmatter is missing required key 'name'")
    else:
        if not SLUG_RE.match(name):
            report.error(
                f"{rel}: name {name!r} must be kebab-case (lowercase letters, digits, hyphens)"
            )
        elif name.startswith("-") or name.endswith("-") or "--" in name:
            report.error(
                f"{rel}: name {name!r} cannot start/end with a hyphen or contain '--'"
            )
        if len(name) > MAX_NAME:
            report.error(f"{rel}: name is {len(name)} chars, limit is {MAX_NAME}")
        if name != skill_dir.name:
            report.error(f"{rel}: name {name!r} does not match directory {skill_dir.name!r}")

    description = meta.get("description")
    if not description:
        report.error(f"{rel}: frontmatter is missing required key 'description'")
    else:
        if len(description) > MAX_DESCRIPTION:
            report.error(
                f"{rel}: description is {len(description)} chars, limit is {MAX_DESCRIPTION}"
            )
        if "<" in description or ">" in description:
            report.error(f"{rel}: description cannot contain angle brackets ('<' or '>')")

    compatibility = meta.get("compatibility")
    if compatibility and len(compatibility) > MAX_COMPATIBILITY:
        report.error(
            f"{rel}: compatibility is {len(compatibility)} chars, limit is {MAX_COMPATIBILITY}"
        )

    for key in meta:
        if key in UPLOAD_ALLOWED_KEYS:
            continue
        if key in LOCAL_ONLY_KEYS:
            if for_upload:
                report.error(
                    f"{rel}: frontmatter key {key!r} is valid for local Claude Code but "
                    "rejected on upload"
                )
            continue
        report.error(f"{rel}: unrecognized frontmatter key {key!r}")

    if not body.strip():
        report.error(f"{rel}: body is empty")
    elif not re.search(r"^#\s+\S", body, re.MULTILINE):
        report.warn(f"{rel}: body has no top-level '# ' heading")


def check_referenced_paths(text: str, label: str, report: Report, repo_root: Path) -> None:
    """Flag repo-relative paths in text that do not exist on disk."""
    for match in REPO_PATH_RE.findall(text):
        # Only '.' can trail a match, since the character class excludes the
        # other sentence punctuation.
        candidate = match.rstrip(".")
        if not (repo_root / candidate).exists():
            report.error(f"{label}: references missing path {candidate!r}")


def check_evals(skill_dir: Path, report: Report, repo_root: Path) -> None:
    evals_path = skill_dir / "evals" / "evals.json"
    rel = rel_to_repo(evals_path)
    if not evals_path.is_file():
        report.warn(f"{rel}: no eval suite found")
        return

    try:
        data = json.loads(evals_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.error(f"{rel}: invalid JSON — {exc}")
        return

    if data.get("skill_name") != skill_dir.name:
        report.error(
            f"{rel}: skill_name {data.get('skill_name')!r} != directory {skill_dir.name!r}"
        )

    cases = data.get("evals")
    if not isinstance(cases, list) or not cases:
        report.error(f"{rel}: 'evals' must be a non-empty list")
        return

    seen_ids: set[object] = set()
    seen_names: set[object] = set()
    for index, case in enumerate(cases):
        label = f"{rel}: eval[{index}]"
        if not isinstance(case, dict):
            report.error(f"{label}: expected an object")
            continue

        for field in ("id", "eval_name", "prompt", "expected_output"):
            value = case.get(field)
            if value is None or value == "":
                report.error(f"{label}: missing required field {field!r}")

        for field, seen in (("id", seen_ids), ("eval_name", seen_names)):
            value = case.get(field)
            if value in seen:
                report.error(f"{label}: duplicate {field} {value!r}")
            seen.add(value)

        # Every fixture a prompt points at must actually exist, otherwise the
        # eval silently tests nothing.
        check_referenced_paths(str(case.get("prompt", "")), f"{label}: prompt", report, repo_root)

        # `files` is a list of attachments the harness feeds the run. Typos
        # there are as silent as typos in the prompt.
        files = case.get("files", [])
        if not isinstance(files, list):
            report.error(f"{label}: 'files' must be a list")
        else:
            for entry in files:
                if not isinstance(entry, str):
                    report.error(f"{label}: 'files' entries must be strings, got {entry!r}")
                    continue
                check_referenced_paths(entry, f"{label}: files", report, repo_root)


def check_no_leaked_paths(report: Report, repo_root: Path) -> None:
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in {".git", "dist", "__pycache__"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if LEAK_EXEMPT_TOKEN in text:
            continue
        for match in sorted(set(HOME_PATH_RE.findall(text))):
            report.error(
                f"{path.relative_to(repo_root)}: absolute local path {match!r} — "
                "use a repo-relative path so the repo stays portable"
            )


def validate(
    skill_dir: Path,
    *,
    repo_root: Path | None = None,
    for_upload: bool = False,
    scan_repo: bool = True,
) -> Report:
    """Validate a skill directory and return a Report."""
    repo_root = repo_root or REPO_ROOT
    report = Report()
    check_skill(skill_dir, report, for_upload=for_upload)
    check_evals(skill_dir, report, repo_root)
    if scan_repo:
        check_no_leaked_paths(report, repo_root)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    parser.add_argument(
        "--for-upload",
        action="store_true",
        help="apply the stricter rules the Skills API enforces on upload",
    )
    args = parser.parse_args(argv)

    skill_dir = resolve_skill_dir(args.skill)
    if skill_dir is None:
        return 2

    report = validate(skill_dir, for_upload=args.for_upload)
    report.render()

    if report.errors:
        print(
            f"\n{len(report.errors)} error(s), {len(report.warnings)} warning(s)",
            file=sys.stderr,
        )
        return 1

    mode = "upload rules" if args.for_upload else "local rules"
    print(f"\nOK — {skill_dir.name} validated ({mode}), {len(report.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
