#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=sleap_common.sh
source "$SCRIPT_DIR/sleap_common.sh"

progress() {
  local step="$1"
  local total="$2"
  local message="$3"
  local pct=$((step * 100 / total))
  local filled=$((pct / 5))
  local empty=$((20 - filled))
  local bar=""
  local i
  for ((i = 0; i < filled; i++)); do
    bar="${bar}#"
  done
  for ((i = 0; i < empty; i++)); do
    bar="${bar}-"
  done
  printf '[install %3d%%] [%s] %s\n' "$pct" "$bar" "$message"
}

check_env() {
  local failed=0
  progress 1 4 "Checking remote Python environment"
  if [[ ! -x "$SLEAP_ENV/bin/python" ]]; then
    echo "FAIL: missing $SLEAP_ENV/bin/python"
    failed=1
  else
    progress 2 4 "Checking SLEAP imports and PyTorch CUDA build"
    "$SLEAP_ENV/bin/python" -u - <<'PY' || failed=1
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

  progress 3 4 "Checking Great Lakes task directories"
  ensure_work_dirs
  if [[ "$failed" -eq 0 ]]; then
    progress 4 4 "Great Lakes SLEAP environment is ready"
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

  progress 1 9 "Loading Python module on Great Lakes"
  maybe_load_python_module
  progress 2 9 "Checking Python version"
  python3 -u - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ required, found {sys.version.split()[0]}")
PY

  progress 3 9 "Checking for incomplete virtual environment"
  if [[ -d "$env_dir" && ! -x "$env_dir/bin/python" ]]; then
    local backup="${env_dir}.broken.$(date +%Y%m%d_%H%M%S)"
    echo "Existing env is incomplete; moving aside: $env_dir -> $backup"
    mv "$env_dir" "$backup"
  fi

  progress 4 9 "Creating virtual environment if needed"
  if [[ ! -x "$env_dir/bin/python" ]]; then
    mkdir -p "$(dirname "$env_dir")"
    python3 -m venv "$env_dir"
  fi

  progress 5 9 "Upgrading pip, wheel, and setuptools"
  "$env_dir/bin/python" -m pip install --progress-bar on --upgrade pip wheel "setuptools<82"
  progress 6 9 "Writing PyTorch constraints"
  constraint_file="$(mktemp)"
  cat > "$constraint_file" <<EOF
torch==${torch_version}+${torch_build}
torchvision==${torchvision_version}+${torch_build}
EOF

  progress 7 9 "Installing PyTorch ${torch_version}+${torch_build} and torchvision ${torchvision_version}+${torch_build}"
  echo "This step can take several minutes on a first install."
  "$env_dir/bin/pip" install \
    --progress-bar on \
    --index-url "$torch_index_url" \
    "torch==${torch_version}+${torch_build}" \
    "torchvision==${torchvision_version}+${torch_build}"
  progress 8 9 "Installing sleap[nn]==1.6.0"
  echo "This step can take several minutes on a first install."
  "$env_dir/bin/pip" install \
    --progress-bar on \
    --extra-index-url "$torch_index_url" \
    --constraint "$constraint_file" \
    "sleap[nn]==1.6.0"
  progress 9 9 "Cleaning temporary install files"
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
