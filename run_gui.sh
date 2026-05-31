#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="${SLEAP_GUI_ENV:-$HOME/sleap_gui_env}"

if [[ -x "$ENV_DIR/bin/python" ]]; then
  PYTHON_BIN="$ENV_DIR/bin/python"
elif [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$PYTHON_BIN"
else
  PYTHON_BIN=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Could not find Python. Run: bash gl_sync/install_local_gui.sh" >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ required, found {sys.version.split()[0]}. Run: bash gl_sync/install_local_gui.sh")
PY

exec "$PYTHON_BIN" "$SCRIPT_DIR/gl_sync/sleap_pipeline_gui.py"
