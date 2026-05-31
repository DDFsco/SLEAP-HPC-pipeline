#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=sleap_common.sh
source "$SCRIPT_DIR/sleap_common.sh"

PRESET="default"
if [[ "${1:-}" == "--preset" ]]; then
  PRESET="${2:-default}"
  shift 2
fi

resolve_inference_config() {
  local name="$1"
  local candidate
  case "$name" in
    ""|*/*|*..*)
      echo "Invalid inference config name: $name" >&2
      return 1
      ;;
  esac
  for candidate in \
    "$SCRIPT_DIR/inference/$name.conf" \
    "$SCRIPT_DIR/inference/$name.env" \
    "$SCRIPT_DIR/inference/$name.sh"; do
    if [[ -f "$candidate" ]]; then
      printf "%s\n" "$candidate"
      return 0
    fi
  done
  echo "Inference config not found: $name" >&2
  echo "Expected one of: $SCRIPT_DIR/inference/$name.conf, .env, or .sh" >&2
  return 1
}

VIDEO_REL="${1:-}"
MODEL_REL="${2:-}"

if [[ -z "$VIDEO_REL" || -z "$MODEL_REL" ]]; then
  echo "Usage: bash predict.sh [--preset preset_name] videos/<video> models/<model>" >&2
  exit 2
fi

WORK="$(resolve_work)"
ensure_work_dirs
INFERENCE_CONFIG="$(resolve_inference_config "$PRESET")"

VIDEO_PATH="$WORK/$VIDEO_REL"
MODEL_PATH="$WORK/$MODEL_REL"
BASE="$(basename "$VIDEO_PATH")"
STEM="${BASE%.*}"
OUT_PATH="$WORK/exports/${STEM}.predicted.slp"
JOB_DIR="$WORK/jobs"
LOG_DIR="$WORK/logs"
JOB_FILE="$JOB_DIR/predict_${STEM}.sbatch"

if [[ ! -f "$VIDEO_PATH" ]]; then
  echo "Video not found: $VIDEO_PATH" >&2
  exit 1
fi
if [[ ! -e "$MODEL_PATH" ]]; then
  echo "Model not found: $MODEL_PATH" >&2
  exit 1
fi

mkdir -p "$WORK/exports" "$JOB_DIR" "$LOG_DIR"

cat > "$JOB_FILE" <<EOF
#!/usr/bin/env bash
#SBATCH --job-name=sleap_predict_${STEM}
#SBATCH --partition=${SLEAP_PARTITION}
#SBATCH --gres=gpu:${SLEAP_GPUS}
#SBATCH --cpus-per-task=${SLEAP_CPUS}
#SBATCH --mem=${SLEAP_MEM}
#SBATCH --time=${SLEAP_TIME}
#SBATCH --output=${LOG_DIR}/predict_${STEM}_%j.out
#SBATCH --error=${LOG_DIR}/predict_${STEM}_%j.err
$(sbatch_account_args)
$(sbatch_mail_args)

set -euo pipefail
export SLEAP_SCRATCH_DIR="${WORK}"
export SLEAP_PRESET="${PRESET}"
export SLEAP_INFERENCE_CONFIG="${INFERENCE_CONFIG}"
source "${SCRIPT_DIR}/sleap_common.sh"
maybe_load_python_module
activate_sleap_env
# shellcheck source=/dev/null
source "${INFERENCE_CONFIG}"

if [[ -n "\${SLEAP_PREDICT_CMD_TEMPLATE:-}" ]]; then
  eval "\${SLEAP_PREDICT_CMD_TEMPLATE}"
else
  sleap track --data_path "${VIDEO_PATH}" --model_paths "${MODEL_PATH}" --output_path "${OUT_PATH}" \${SLEAP_TRACK_ARGS:-}
fi
EOF

sbatch "$JOB_FILE"
