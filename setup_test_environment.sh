#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <target_path>" >&2
  exit 1
fi

abspath() {
  python3 -c "import os, sys; print(os.path.abspath(sys.argv[1]))" "$1"
}

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="$(abspath "$1")"

if [[ "$TARGET_ROOT" == "$SOURCE_ROOT" ]]; then
  echo "Target path cannot be the current project directory." >&2
  exit 1
fi

if [[ "$TARGET_ROOT" == "$SOURCE_ROOT"/* ]]; then
  echo "Target path cannot be inside the current project directory. Choose a different location." >&2
  exit 1
fi

mkdir -p "$TARGET_ROOT"

if [[ -n "$(ls -A "$TARGET_ROOT" 2>/dev/null)" ]]; then
  echo "Target directory is not empty: $TARGET_ROOT"
  read -r -p "Type YES to clean it and continue: " confirmation
  if [[ "$confirmation" != "YES" ]]; then
    echo "Aborted. No files were changed."
    exit 1
  fi
  find "$TARGET_ROOT" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.db' \
    "$SOURCE_ROOT/" "$TARGET_ROOT/"
else
  echo "rsync was not found. Install rsync and try again." >&2
  exit 1
fi

echo "Project copied to: $TARGET_ROOT"
echo "Next steps:"
echo "1) cd '$TARGET_ROOT'"
echo "2) ./run_demo_backend.sh"
