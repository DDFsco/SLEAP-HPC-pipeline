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
import torch
torch_version = getattr(torch, "__version__", "")
cuda_version = getattr(torch.version, "cuda", None)
print(f"torch={torch_version} cuda={cuda_version}")
expected_build = "cu121"
if expected_build not in torch_version:
    raise SystemExit(
        f"torch build {torch_version!r} is not the expected +{expected_build} build for Great Lakes V100 GPUs"
    )
PY
  fi

  ensure_work_dirs
  if [[ "$failed" -eq 0 ]]; then
    echo "OK: GL SLEAP environment and work directories look ready."
  fi
  return "$failed"
}

install_env() {
  local env_dir="$1"
  local torch_build="${SLEAP_TORCH_BUILD:-cu121}"
  local torch_version="${SLEAP_TORCH_VERSION:-2.5.1}"
  local torchvision_version="${SLEAP_TORCHVISION_VERSION:-0.20.1}"
  local torch_index_url="${SLEAP_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu121}"
  local constraint_file

  maybe_load_python_module
  python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ required, found {sys.version.split()[0]}")
PY

  if [[ -d "$env_dir" && ! -x "$env_dir/bin/python" ]]; then
    local backup="${env_dir}.broken.$(date +%Y%m%d_%H%M%S)"
    echo "Existing env is incomplete; moving aside: $env_dir -> $backup"
    mv "$env_dir" "$backup"
  fi

  if [[ ! -x "$env_dir/bin/python" ]]; then
    mkdir -p "$(dirname "$env_dir")"
    python3 -m venv "$env_dir"
  fi

  "$env_dir/bin/python" -m pip install --upgrade pip wheel "setuptools<82"
  constraint_file="$(mktemp)"
  cat > "$constraint_file" <<EOF
torch==${torch_version}+${torch_build}
torchvision==${torchvision_version}+${torch_build}
EOF

  "$env_dir/bin/pip" install \
    --index-url "$torch_index_url" \
    "torch==${torch_version}+${torch_build}" \
    "torchvision==${torchvision_version}+${torch_build}"
  "$env_dir/bin/pip" install \
    --extra-index-url "$torch_index_url" \
    --constraint "$constraint_file" \
    "sleap[nn]==1.6.0"
  rm -f "$constraint_file"
}

if [[ "${1:-}" == "--check" ]]; then
  check_env
  exit $?
fi

echo "Installing/checking SLEAP on Great Lakes."
ensure_work_dirs
install_env "$SLEAP_ENV"
check_env
