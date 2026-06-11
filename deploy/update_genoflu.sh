#!/usr/bin/env bash
# update_genoflu.sh — bump the GenoFLU conda package (tool + bundled reference
# DB) to the latest bioconda release, then verify and report the new version.
#
# GenoFLU's reference DB (segment FASTAs + genotype_key.xlsx) is shipped inside
# the conda package and is the thing that defines which genotypes can be called.
# USDA-VS releases updates as new genotypes emerge; this script is the single,
# reproducible way to pick them up. The version in use is recorded in every
# run's run_manifest.json, so after updating, prior reports still document the
# DB version they were produced with.
#
# Usage:
#   deploy/update_genoflu.sh [--personal] [--conda-base DIR] [--version X.YZ] [--dry-run]
#
# By default it updates to the newest available `genoflu`. Pass --version to pin
# a specific release (e.g. --version 1.07). Re-run install.sh is NOT needed; the
# next pipeline run uses the updated package immediately.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARED_ENV="${REPO_DIR}/env"
PERSONAL_ENV_NAME="genoflu_gui"
CONDA_BASE="${HOME}/miniforge3"
USE_PERSONAL=0
PIN_VERSION=""
DRY_RUN=0

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --personal)    USE_PERSONAL=1; shift;;
    --conda-base)  CONDA_BASE="$2"; shift 2;;
    --version)     PIN_VERSION="$2"; shift 2;;
    --dry-run)     DRY_RUN=1; shift;;
    -h|--help)     sed -n '2,20p' "$0"; exit 0;;
    *) die "unknown arg: $1";;
  esac
done

CONDA="${CONDA_BASE}/bin/conda"
[[ -x "${CONDA}" ]] || CONDA="$(command -v conda 2>/dev/null || true)"
[[ -n "${CONDA}" && -x "${CONDA}" ]] || die "conda not found. Pass --conda-base."
MAMBA="${CONDA_BASE}/bin/mamba"
[[ -x "${MAMBA}" ]] || MAMBA="$(command -v mamba 2>/dev/null || echo "${CONDA}")"

if [[ ${USE_PERSONAL} -eq 1 ]]; then
  ENV_REF=("-n" "${PERSONAL_ENV_NAME}")
  ENV_BIN="$("${CONDA}" run -n "${PERSONAL_ENV_NAME}" sh -c 'echo $CONDA_PREFIX/bin')"
else
  ENV_REF=("-p" "${SHARED_ENV}")
  ENV_BIN="${SHARED_ENV}/bin"
fi
[[ -x "${ENV_BIN}/genoflu.py" || -x "${ENV_BIN}/genoflu" ]] || \
  die "GenoFLU env not found. Run deploy/install.sh first."

GENOFLU="${ENV_BIN}/genoflu.py"; [[ -x "${GENOFLU}" ]] || GENOFLU="${ENV_BIN}/genoflu"
log "Current GenoFLU: $("${GENOFLU}" -v 2>&1 | head -1)"
GENO_REAL="$(readlink -f "${GENOFLU}")"
DEP_DIR="$(cd "$(dirname "${GENO_REAL}")/.." && pwd)/dependencies"
[[ -f "${DEP_DIR}/genotype_key.xlsx" ]] && \
  log "Current genotype_key.xlsx dated: $(date -r "${DEP_DIR}/genotype_key.xlsx" +%Y-%m-%d)"

SPEC="genoflu"
[[ -n "${PIN_VERSION}" ]] && SPEC="genoflu=${PIN_VERSION}"
log "Updating ${SPEC} via ${MAMBA} (channels conda-forge + bioconda)"
run "${MAMBA}" install "${ENV_REF[@]}" -c conda-forge -c bioconda "${SPEC}" -y

log "New GenoFLU: $("${GENOFLU}" -v 2>&1 | head -1)"
GENO_REAL="$(readlink -f "${GENOFLU}")"
DEP_DIR="$(cd "$(dirname "${GENO_REAL}")/.." && pwd)/dependencies"
if [[ -d "${DEP_DIR}/fastas" && -f "${DEP_DIR}/genotype_key.xlsx" ]]; then
  n_fastas=$(find "${DEP_DIR}/fastas" -maxdepth 1 -type f | wc -l | tr -d ' ')
  ok "reference DB OK: ${DEP_DIR} (${n_fastas} reference FASTAs)"
  ok "genotype_key.xlsx dated: $(date -r "${DEP_DIR}/genotype_key.xlsx" +%Y-%m-%d)"
else
  warn "reference DB not found under ${DEP_DIR} after update — check the package."
fi

echo
echo "To pin this version in the repo, edit conda_setup/environment.yml:"
echo "    - genoflu>=$( "${GENOFLU}" -v 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1 )"
echo "The next pipeline run uses the updated package immediately (no rebuild needed)."
