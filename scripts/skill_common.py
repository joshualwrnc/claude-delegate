#!/usr/bin/env python3
"""Shared helpers for the skill validator and packager.

Both scripts need the same three things, and drift between their copies is a
silent correctness bug: the packager would ship files the validator never
inspected, or the validator would police files that never make it into a
`.skill`. Single source of truth for:

  * the packaging exclusion rules (`is_packaged`),
  * repo-relative labels for messages (`rel_to_repo`),
  * the `<script> [skill]` CLI front end (`build_parser`, `resolve_skill_dir`).

Standard library only, so CI needs no install step.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SKILL = "delegate"
SKILL_FILENAME = "SKILL.md"

# Directories whose contents are not packaged into a `.skill`. `evals` is
# excluded only at the skill root; the others at any depth. Mirrors
# Anthropic's packager, so what the validator counts matches what upload sees.
EXCLUDED_DIRS = {"__pycache__", "node_modules"}
ROOT_EXCLUDED_DIRS = {"evals"}
EXCLUDED_FILES = {".DS_Store"}
EXCLUDED_FILE_GLOBS = {"*.pyc"}


def is_packaged(rel_path: Path) -> bool:
    """True if rel_path (relative to the skill root) ends up in a `.skill`."""
    dir_parts = rel_path.parts[:-1]
    if any(part in EXCLUDED_DIRS for part in dir_parts):
        return False
    if dir_parts and dir_parts[0] in ROOT_EXCLUDED_DIRS:
        return False
    if rel_path.name in EXCLUDED_FILES:
        return False
    return not any(fnmatch.fnmatch(rel_path.name, pattern) for pattern in EXCLUDED_FILE_GLOBS)


def rel_to_repo(path: Path, repo_root: Path = REPO_ROOT) -> Path:
    """Path relative to repo_root for use in messages, or path itself.

    Skill directories under a temporary root (tests, ad-hoc validation) are
    not under repo_root, and `relative_to` raises there.
    """
    try:
        return path.relative_to(repo_root)
    except ValueError:
        return path


def build_parser(doc: str | None) -> argparse.ArgumentParser:
    """Argument parser with the `[skill]` positional both scripts accept."""
    summary = doc.splitlines()[0] if doc else None
    parser = argparse.ArgumentParser(description=summary)
    parser.add_argument("skill", nargs="?", default=DEFAULT_SKILL, help="skill directory")
    return parser


def resolve_skill_dir(skill: str) -> Path | None:
    """Resolve a `skill` argument against the repo root, or report and fail."""
    skill_dir = (REPO_ROOT / skill).resolve()
    if not skill_dir.is_dir():
        print(f"error: {skill_dir} is not a directory", file=sys.stderr)
        return None
    return skill_dir
