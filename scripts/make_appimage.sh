#!/usr/bin/env bash
# Thin wrapper around scripts/build_appimage.py.
# Examples:
#   scripts/make_appimage.sh --cli-only --stage-only --appdir /tmp/vm-appdir
#   scripts/make_appimage.sh --cli-only
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi
exec "$PYTHON" "$ROOT/scripts/build_appimage.py" "$@"
