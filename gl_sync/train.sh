#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=sleap_common.sh
source "$SCRIPT_DIR/sleap_common.sh"

ZIP_NAME="${1:-}"
RUN_NAME="${2:-}"

if [[ -z "$ZIP_NAME" || -z "$RUN_NAME" ]]; then
  echo "Usage: bash train.sh <training_job_zip_basename> <run_name>" >&2
  exit 2
fi

WORK="$(resolve_work)"
ensure_work_dirs

ZIP_PATH="$WORK/training_package/$ZIP_NAME"
MODEL_DIR="$WORK/models/$RUN_NAME"
JOB_DIR="$WORK/jobs"
LOG_DIR="$WORK/logs"
JOB_FILE="$JOB_DIR/train_${RUN_NAME}.sbatch"

if [[ ! -f "$ZIP_PATH" ]]; then
  echo "Training package not found: $ZIP_PATH" >&2
  exit 1
fi

mkdir -p "$MODEL_DIR" "$JOB_DIR" "$LOG_DIR"

cat > "$JOB_FILE" <<EOF
#!/usr/bin/env bash
#SBATCH --job-name=sleap_train_${RUN_NAME}
#SBATCH --partition=${SLEAP_PARTITION}
#SBATCH --gres=gpu:${SLEAP_GPUS}
#SBATCH --cpus-per-task=${SLEAP_CPUS}
#SBATCH --mem=${SLEAP_MEM}
#SBATCH --time=${SLEAP_TIME}
#SBATCH --output=${LOG_DIR}/train_${RUN_NAME}_%j.out
#SBATCH --error=${LOG_DIR}/train_${RUN_NAME}_%j.err
$(sbatch_account_args)
$(sbatch_mail_args)

set -euo pipefail
export SLEAP_SCRATCH_DIR="${WORK}"
source "${SCRIPT_DIR}/sleap_common.sh"
maybe_load_python_module
activate_sleap_env
mkdir -p "${MODEL_DIR}"

if [[ -n "\${SLEAP_TRAIN_CMD_TEMPLATE:-}" ]]; then
  eval "\${SLEAP_TRAIN_CMD_TEMPLATE}"
else
  sleap train "${ZIP_PATH}" --output "${MODEL_DIR}"
fi
EOF

sbatch "$JOB_FILE"
