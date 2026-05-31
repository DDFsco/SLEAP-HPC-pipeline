#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=sleap_common.sh
source "$SCRIPT_DIR/sleap_common.sh"

check_env() {
  local failed=0
  if [[ ! -x "$SLEAP_ENV/bin/python" ]]; then
    echo "FAIL: missing $SLEAP_ENV/bin/python"
    failed=1
  else
    "$SLEAP_ENV/bin/python" - <<'PY' || failed=1
import importlib.util
missing = [name for name in ("sleap", "sleap_nn") if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("missing imports: " + ", ".join(missing))
PY
  fi

  if [[ ! -x "$SLEAP_GUI_ENV/bin/python" ]]; then
    echo "FAIL: missing $SLEAP_GUI_ENV/bin/python"
    failed=1
  fi

  ensure_work_dirs
  if [[ "$failed" -eq 0 ]]; then
    echo "OK: GL SLEAP environments and work directories look ready."
  fi
  return "$failed"
}

install_env() {
  local env_dir="$1"
  maybe_load_python_module
  python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ required, found {sys.version.split()[0]}")
PY
  python3 -m venv "$env_dir"
  "$env_dir/bin/python" -m pip install --upgrade pip wheel "setuptools<82"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$env_dir/bin/python" "sleap[nn]==1.6.0"
  else
    "$env_dir/bin/pip" install "sleap[nn]==1.6.0"
  fi
}

if [[ "${1:-}" == "--check" ]]; then
  check_env
  exit $?
fi

echo "Installing/checking SLEAP on Great Lakes."
ensure_work_dirs
install_env "$SLEAP_ENV"
install_env "$SLEAP_GUI_ENV"
check_env
