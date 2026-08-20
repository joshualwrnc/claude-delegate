#!/usr/bin/env bash
# Install the delegate skill for Claude Code.
#
#   ./install.sh                  # personal: ~/.claude/skills/delegate  (all projects)
#   ./install.sh --project        # project:  ./.claude/skills/delegate  (this repo only)
#   ./install.sh --project PATH   # project:  PATH/.claude/skills/delegate
#   ./install.sh --force          # overwrite an existing install
#   ./install.sh --uninstall      # remove it again
#
# The skill body is a single SKILL.md; evals are not installed (they are a
# development aid, not part of the skill).

set -euo pipefail

# Resolve symlinks so invoking this via a symlinked path still finds the skill.
# readlink -f is GNU; fall back to the raw path where it is unavailable.
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || printf '%s' "${BASH_SOURCE[0]}")"
SRC="$(cd "$(dirname "$SELF")" && pwd)/delegate"
SKILL_NAME="delegate"

scope="personal"
target_root="${HOME}"
force=0
uninstall=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      scope="project"
      if [[ ${2-} && ${2-} != --* ]]; then
        target_root="$2"
        shift
        if [[ ! -d "$target_root" ]]; then
          echo "error: --project path '$target_root' is not a directory" >&2
          exit 1
        fi
      else
        target_root="$(pwd)"
      fi
      ;;
    --force) force=1 ;;
    --uninstall) uninstall=1 ;;
    -h|--help)
      # Print the leading comment block, however long it grows.
      awk 'NR > 1 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "error: unknown argument '$1' (try --help)" >&2
      exit 2
      ;;
  esac
  shift
done

# `set -u` catches an *unset* HOME but not an empty one, and stripping the
# trailing slash off "/" also yields "". Either way dest would become
# /.claude/skills/delegate — writing to, or rm -rf'ing, the filesystem root.
# Check before dest is used by any branch below, including --uninstall.
target_root="${target_root%/}"
if [[ -z "$target_root" ]]; then
  echo "error: refusing to operate on the filesystem root — HOME or --project is empty or '/'" >&2
  exit 1
fi

dest="${target_root}/.claude/skills/${SKILL_NAME}"

if [[ $uninstall -eq 1 ]]; then
  if [[ -e "$dest" ]]; then
    rm -rf "$dest"
    echo "removed $dest"
  else
    echo "nothing to remove at $dest"
  fi
  exit 0
fi

if [[ ! -f "${SRC}/SKILL.md" ]]; then
  echo "error: ${SRC}/SKILL.md not found — run this from a clone of the repo" >&2
  exit 1
fi

if [[ -e "$dest" && $force -eq 0 ]]; then
  echo "error: $dest already exists (re-run with --force to overwrite)" >&2
  exit 1
fi

mkdir -p "$(dirname "$dest")"
rm -rf "$dest"
mkdir -p "$dest"
cp "${SRC}/SKILL.md" "${dest}/SKILL.md"

echo "installed ${SKILL_NAME} (${scope}) → ${dest}"
echo "Claude Code picks it up without a restart; run /delegate to invoke it."
