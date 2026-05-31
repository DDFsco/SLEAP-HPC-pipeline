#!/usr/bin/env bash
set -euo pipefail

SLEAP_CONF="${SLEAP_CONF:-$HOME/sleap_gl.conf}"

if [[ -f "$SLEAP_CONF" ]]; then
  # shellcheck source=/dev/null
  source "$SLEAP_CONF"
fi

SLEAP_SLURM_ACCOUNT="${SLEAP_SLURM_ACCOUNT:-${SLURM_ACCOUNT:-}}"
SLEAP_MAIL_USER="${SLEAP_MAIL_USER:-}"
SLEAP_PARTITION="${SLEAP_PARTITION:-gpu}"
SLEAP_GPUS="${SLEAP_GPUS:-v100:1}"
SLEAP_TIME="${SLEAP_TIME:-08:00:00}"
SLEAP_MEM="${SLEAP_MEM:-32G}"
SLEAP_CPUS="${SLEAP_CPUS:-4}"

resolve_work() {
  if [[ -n "${SLEAP_SCRATCH_DIR:-}" ]]; then
    printf "%s\n" "$SLEAP_SCRATCH_DIR"
    return
  fi
  if [[ -n "${SLEAP_WORK:-}" ]]; then
    printf "%s\n" "$SLEAP_WORK"
    return
  fi
  if [[ -z "${USER:-}" ]]; then
    echo "USER is not set and SLEAP_SCRATCH_DIR/SLEAP_WORK were not provided." >&2
    return 1
  fi
  printf "/scratch/gid_root/gid0/%s/sleap_rat\n" "$USER"
}

SLEAP_WORK_RESOLVED="$(resolve_work)"
SLEAP_ENV="${SLEAP_ENV:-$SLEAP_WORK_RESOLVED/env/sleap_env}"

ensure_work_dirs() {
  mkdir -p "$SLEAP_WORK_RESOLVED"/{labels,training_package,models,videos,exports,logs,jobs,env}
}

activate_sleap_env() {
  if [[ ! -f "$SLEAP_ENV/bin/activate" ]]; then
    echo "Missing SLEAP env: $SLEAP_ENV" >&2
    echo "Run: bash ~/gl_sync/install.sh" >&2
    return 1
  fi
  # shellcheck source=/dev/null
  source "$SLEAP_ENV/bin/activate"
}

maybe_load_python_module() {
  if command -v module >/dev/null 2>&1; then
    module load python/3.11 >/dev/null 2>&1 || true
  fi
}

sbatch_account_args() {
  if [[ -n "$SLEAP_SLURM_ACCOUNT" ]]; then
    printf "#SBATCH --account=%s\n" "$SLEAP_SLURM_ACCOUNT"
  fi
}

sbatch_mail_args() {
  if [[ -n "$SLEAP_MAIL_USER" ]]; then
    printf "#SBATCH --mail-user=%s\n#SBATCH --mail-type=END,FAIL\n" "$SLEAP_MAIL_USER"
  fi
}
