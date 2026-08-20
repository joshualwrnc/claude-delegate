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

import sys
import zipfile
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
from validate_skill import validate  # noqa: E402

# Fixed timestamp for reproducible archives (zip epoch starts at 1980).
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def should_exclude(arcname: Path) -> bool:
    """Decide exclusion from an arcname rooted at the skill folder name."""
    # The shared rule is expressed relative to the skill root, so drop the
    # leading skill-folder component the archive adds.
    return not is_packaged(Path(*arcname.parts[1:]))


def package(skill_dir: Path, out_dir: Path, *, quiet: bool = False) -> Path:
    """Package skill_dir into out_dir/<name>.skill. Returns the archive path."""
    skill_dir = skill_dir.resolve()
    if not (skill_dir / SKILL_FILENAME).is_file():
        raise FileNotFoundError(f"{skill_dir}/{SKILL_FILENAME} not found")

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
        if not path.is_file():
            continue
        arcname = path.relative_to(skill_dir.parent)
        if should_exclude(arcname):
            if not quiet:
                print(f"  skipped: {arcname}")
            continue
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

    if f"{skill_name}/{SKILL_FILENAME}" not in names:
        raise ValueError(f"{archive.name}: missing {skill_name}/{SKILL_FILENAME}")

    skill_mds = [n for n in names if n.endswith(SKILL_FILENAME)]
    if len(skill_mds) != 1:
        raise ValueError(
            f"{archive.name}: expected exactly one {SKILL_FILENAME}, "
            f"found {len(skill_mds)}: {skill_mds}"
        )

    leaked = [n for n in names if "/evals/" in f"/{n}"]
    if leaked:
        raise ValueError(f"{archive.name}: evals must not be packaged: {leaked}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    parser.add_argument("--out-dir", default="dist", help="output directory (default: dist)")
    parser.add_argument("--quiet", action="store_true", help="only print the result path")
    args = parser.parse_args(argv)

    skill_dir = resolve_skill_dir(args.skill)
    if skill_dir is None:
        return 2
    out_dir = (REPO_ROOT / args.out_dir).resolve()

    try:
        archive = package(skill_dir, out_dir, quiet=args.quiet)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    size = archive.stat().st_size
    print(f"\npackaged {rel_to_repo(archive)} ({size:,} bytes)")
    print("Upload it at claude.ai -> Settings -> Capabilities -> Skills.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
