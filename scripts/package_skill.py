#!/usr/bin/env python3
"""Package the skill into a distributable `.skill` file.

A `.skill` is a zip archive whose entries are rooted at the skill folder name
(`delegate/SKILL.md`, ...). Upload it to your Claude account under
Settings -> Capabilities -> Skills, or hand it to an org owner to provision
organization-wide.

Exclusions mirror Anthropic's own packager: `evals/` is dropped at the skill
root (it is a development aid, not part of the skill), and `__pycache__`,
`node_modules`, `*.pyc`, and `.DS_Store` are dropped at any depth.

Output is byte-reproducible: entries are sorted and timestamps fixed, so an
unchanged skill always produces an identical archive.

Usage:
    python3 scripts/package_skill.py                   # -> dist/delegate.skill
    python3 scripts/package_skill.py --out-dir build
    python3 scripts/package_skill.py delegate --out-dir dist
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_skill import REPO_ROOT, validate  # noqa: E402

EXCLUDE_DIRS = {"__pycache__", "node_modules"}
ROOT_EXCLUDE_DIRS = {"evals"}
EXCLUDE_GLOBS = {"*.pyc"}
EXCLUDE_FILES = {".DS_Store"}

# Fixed timestamp for reproducible archives (zip epoch starts at 1980).
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def should_exclude(arcname: Path) -> bool:
    """Decide exclusion from an arcname rooted at the skill folder name."""
    parts = arcname.parts
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    # parts[0] is the skill folder; parts[1] is its first subdirectory.
    if len(parts) > 1 and parts[1] in ROOT_EXCLUDE_DIRS:
        return True
    if arcname.name in EXCLUDE_FILES:
        return True
    return any(fnmatch.fnmatch(arcname.name, pattern) for pattern in EXCLUDE_GLOBS)


def package(skill_dir: Path, out_dir: Path, *, quiet: bool = False) -> Path:
    """Package skill_dir into out_dir/<name>.skill. Returns the archive path."""
    skill_dir = skill_dir.resolve()
    if not (skill_dir / "SKILL.md").is_file():
        raise FileNotFoundError(f"{skill_dir}/SKILL.md not found")

    # Package only what would survive upload — catch problems here rather than
    # shipping an artifact guaranteed to be rejected.
    report = validate(skill_dir, for_upload=True)
    if not report.ok:
        report.render()
        raise ValueError(f"validation failed with {len(report.errors)} error(s)")

    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{skill_dir.name}.skill"

    members: list[tuple[Path, Path]] = []
    for path in sorted(collect_files(skill_dir)):
        arcname = path.relative_to(skill_dir.parent)
        if should_exclude(arcname):
            if not quiet:
                print(f"  skipped: {arcname}")
            continue
        members.append((path, arcname))

    if not members:
        raise ValueError("nothing to package")

    # Build beside the target and swap in only once verify() passes, so a
    # failure never leaves a half-written or invalid .skill where the previous
    # good one was — an artifact that would otherwise be uploaded or committed.
    staged = archive.with_name(f"{archive.name}.tmp")
    try:
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, arcname in members:
                info = zipfile.ZipInfo(str(arcname), date_time=FIXED_DATE_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, path.read_bytes())
                if not quiet:
                    print(f"  added:   {arcname}")

        verify(staged, skill_dir.name, label=archive.name)
        os.replace(staged, archive)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise

    return archive


def collect_files(skill_dir: Path) -> list[Path]:
    """List every file under skill_dir, failing loudly on an unwalkable dir.

    Path.rglob swallows permission errors, which would silently ship a .skill
    missing whatever lived in the directory that could not be read.
    """

    def on_error(exc: OSError) -> None:
        raise exc

    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(skill_dir, onerror=on_error):
        for filename in filenames:
            files.append(Path(dirpath) / filename)
    return files


def verify(archive: Path, skill_name: str, *, label: str | None = None) -> None:
    """Re-open the archive and assert the invariants upload depends on.

    label names the archive in messages, so a staged file can be verified
    while errors still point at the path the caller asked for.
    """
    name = label or archive.name
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise ValueError(f"{name}: corrupt entry {bad}")
        names = zf.namelist()

    expected_root = f"{skill_name}/"
    stray = [n for n in names if not n.startswith(expected_root)]
    if stray:
        raise ValueError(f"{name}: entries outside {expected_root}: {stray}")

    if f"{skill_name}/SKILL.md" not in names:
        raise ValueError(f"{name}: missing {skill_name}/SKILL.md")

    skill_mds = [n for n in names if n.endswith("SKILL.md")]
    if len(skill_mds) != 1:
        raise ValueError(
            f"{name}: expected exactly one SKILL.md, found {len(skill_mds)}: {skill_mds}"
        )

    leaked = [n for n in names if "/evals/" in f"/{n}"]
    if leaked:
        raise ValueError(f"{name}: evals must not be packaged: {leaked}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("skill", nargs="?", default="delegate", help="skill directory")
    parser.add_argument("--out-dir", default="dist", help="output directory (default: dist)")
    parser.add_argument("--quiet", action="store_true", help="only print the result path")
    args = parser.parse_args(argv)

    skill_dir = (REPO_ROOT / args.skill).resolve()
    out_dir = (REPO_ROOT / args.out_dir).resolve()

    if not skill_dir.is_dir():
        print(f"error: {skill_dir} is not a directory", file=sys.stderr)
        return 2

    try:
        archive = package(skill_dir, out_dir, quiet=args.quiet)
        size = archive.stat().st_size
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        shown = archive.relative_to(REPO_ROOT)
    except ValueError:
        shown = archive

    print(f"\npackaged {shown} ({size:,} bytes)")
    print("Upload it at claude.ai -> Settings -> Capabilities -> Skills.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
