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
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_symlink() and not path.is_file():
            continue
        arcname = path.relative_to(skill_dir.parent)
        if should_exclude(arcname):
            if not quiet:
                print(f"  skipped: {arcname}")
            continue
        # A symlink under the skill root would be followed and its *target's*
        # bytes embedded in a shareable archive — an easy way to ship a host
        # file (an .ssh key, an .env) to whoever installs the skill. Refuse
        # rather than silently drop it, so the omission is never a surprise.
        if path.is_symlink():
            raise ValueError(
                f"{arcname} is a symlink; refusing to package (it would embed the "
                "target's contents). Replace it with a regular file or remove it."
            )
        members.append((path, arcname))

    if not members:
        raise ValueError("nothing to package")

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in members:
            info = zipfile.ZipInfo(str(arcname), date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
            if not quiet:
                print(f"  added:   {arcname}")

    verify(archive, skill_dir.name)
    return archive


def verify(archive: Path, skill_name: str) -> None:
    """Re-open the archive and assert the invariants upload depends on."""
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise ValueError(f"{archive.name}: corrupt entry {bad}")
        names = zf.namelist()

    expected_root = f"{skill_name}/"
    stray = [n for n in names if not n.startswith(expected_root)]
    if stray:
        raise ValueError(f"{archive.name}: entries outside {expected_root}: {stray}")

    if f"{skill_name}/SKILL.md" not in names:
        raise ValueError(f"{archive.name}: missing {skill_name}/SKILL.md")

    skill_mds = [n for n in names if n.endswith("SKILL.md")]
    if len(skill_mds) != 1:
        raise ValueError(
            f"{archive.name}: expected exactly one SKILL.md, found {len(skill_mds)}: {skill_mds}"
        )

    leaked = [n for n in names if "/evals/" in f"/{n}"]
    if leaked:
        raise ValueError(f"{archive.name}: evals must not be packaged: {leaked}")


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
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    size = archive.stat().st_size
    print(f"\npackaged {archive.relative_to(REPO_ROOT)} ({size:,} bytes)")
    print("Upload it at claude.ai -> Settings -> Capabilities -> Skills.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
