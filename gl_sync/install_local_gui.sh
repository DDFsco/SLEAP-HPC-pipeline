#!/usr/bin/env bash
set -euo pipefail

ENV_DIR="${SLEAP_GUI_ENV:-$HOME/sleap_gui_env}"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Could not find Python. Install Python 3.11+ first." >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ required, found {sys.version.split()[0]}")
PY

echo "Creating local SLEAP GUI environment at: $ENV_DIR"
"$PYTHON_BIN" -m venv "$ENV_DIR"

if [[ -x "$ENV_DIR/bin/python" ]]; then
  PY="$ENV_DIR/bin/python"
  PIP="$ENV_DIR/bin/pip"
else
  echo "Could not find venv python under $ENV_DIR/bin" >&2
  exit 1
fi

"$PY" -m pip install --upgrade pip wheel setuptools

if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" "sleap[nn]==1.6.0" PySide6==6.4.3 "numpy<2" opencv-python-headless==4.8.1.78
else
  "$PIP" install "sleap[nn]==1.6.0" PySide6==6.4.3 "numpy<2" opencv-python-headless==4.8.1.78
fi

"$PY" - <<'PY'
import importlib.util
import subprocess
import sys

missing = [name for name in ("sleap", "sleap_nn") if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit(f"Missing imports after install: {missing}")

for cmd in (["sleap", "--help"], ["sleap-label", "--help"]):
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            print("SLEAP GUI command:", cmd[0])
            break
    except FileNotFoundError:
        continue
else:
    raise SystemExit("Neither sleap nor sleap-label is available on PATH.")

print("Local SLEAP GUI environment verified.")
PY

echo "Set sleap_label_cmd to one of:"
echo "  $ENV_DIR/bin/sleap"
echo "  $ENV_DIR/bin/sleap-label"
