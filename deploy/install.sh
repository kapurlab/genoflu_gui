#!/usr/bin/env bash
# install.sh — idempotent, no-sudo deployment of the GenoFLU GUI.
#
# Mirrors the Kraken/vSNP/AMR sandbox pattern. Every heavy step is skippable and
# clearly logged. Safe to re-run. Designed to be portable to other OnDemand
# hosts: it takes no hard-coded user paths, prefers a shared env at <repo>/env,
# and verifies the GenoFLU reference DB that ships inside the conda package.
#
# What it does:
#   1. Locate/create the conda env (shared at <repo>/env, else personal genoflu_gui).
#   2. pip install backend/requirements.txt into that env.
#   3. Verify GenoFLU + BLAST + seqkit are present, and that the bundled
#      reference DB (dependencies/fastas + genotype_key.xlsx) resolves.
#   4. Build the React frontend (frontend/dist/).
#
# Usage:
#   deploy/install.sh [--personal] [--conda-base DIR]
#                     [--skip-verify] [--skip-frontend] [--dry-run]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- defaults ----
SHARED_ENV="${REPO_DIR}/env"
PERSONAL_ENV_NAME="genoflu_gui"
CONDA_BASE="${HOME}/miniforge3"
USE_PERSONAL=0
SKIP_VERIFY=0
SKIP_FRONTEND=0
DRY_RUN=0

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --personal)       USE_PERSONAL=1; shift;;
    --conda-base)     CONDA_BASE="$2"; shift 2;;
    --skip-verify)    SKIP_VERIFY=1; shift;;
    --skip-frontend)  SKIP_FRONTEND=1; shift;;
    --dry-run)        DRY_RUN=1; shift;;
    -h|--help)        sed -n '2,30p' "$0"; exit 0;;
    *) die "unknown arg: $1";;
  esac
done

log "GenoFLU GUI install"
echo "  repo:  ${REPO_DIR}"
[[ ${DRY_RUN} -eq 1 ]] && warn "DRY RUN — no changes will be made"

# ---------------------------------------------------------------------------
# 1. conda env
# ---------------------------------------------------------------------------
CONDA="${CONDA_BASE}/bin/conda"
[[ -x "${CONDA}" ]] || CONDA="$(command -v conda 2>/dev/null || true)"
[[ -n "${CONDA}" && -x "${CONDA}" ]] || die "conda not found. Install miniforge to ${CONDA_BASE} or pass --conda-base."
ok "conda: ${CONDA}"
# Prefer mamba — conda's classic solver hangs on big bioconda envs.
CONDA_FRONTEND="${CONDA_FRONTEND:-}"
if [[ -z "${CONDA_FRONTEND}" ]]; then
  if [[ -x "${CONDA_BASE}/bin/mamba" ]]; then CONDA_FRONTEND="${CONDA_BASE}/bin/mamba"
  elif command -v mamba >/dev/null 2>&1; then CONDA_FRONTEND="$(command -v mamba)"
  else CONDA_FRONTEND="${CONDA}"; fi
fi
ok "env builder: ${CONDA_FRONTEND}"

ENV_FILE="${REPO_DIR}/conda_setup/environment.yml"
if [[ ${USE_PERSONAL} -eq 1 ]]; then
  ENV_BIN="$("${CONDA}" run -n "${PERSONAL_ENV_NAME}" sh -c 'echo $CONDA_PREFIX/bin' 2>/dev/null || true)"
  ENV_DESC="personal env ${PERSONAL_ENV_NAME}"
  ENV_EXISTS=$("${CONDA}" env list | awk '{print $1}' | grep -qx "${PERSONAL_ENV_NAME}" && echo 1 || echo 0)
  CREATE_FLAG=("-n" "${PERSONAL_ENV_NAME}")
else
  ENV_BIN="${SHARED_ENV}/bin"
  ENV_DESC="shared env ${SHARED_ENV}"
  ENV_EXISTS=$([[ -x "${SHARED_ENV}/bin/python" ]] && echo 1 || echo 0)
  CREATE_FLAG=("-p" "${SHARED_ENV}")
fi

if [[ "${ENV_EXISTS}" -eq 1 ]]; then
  ok "${ENV_DESC} already exists — skipping create"
else
  # A cancelled mid-solve leaves a partial env dir with no python; clear it.
  if [[ ${USE_PERSONAL} -eq 0 && -d "${SHARED_ENV}" ]]; then
    warn "removing incomplete env at ${SHARED_ENV} (no python found)"
    run rm -rf "${SHARED_ENV}"
  fi
  log "creating ${ENV_DESC} from ${ENV_FILE} (solve can take 2-5 min)"
  run "${CONDA_FRONTEND}" env create "${CREATE_FLAG[@]}" -f "${ENV_FILE}"
fi

PYTHON="${ENV_BIN}/python"
# Put the env's bin on PATH for every tool call below. genoflu.py shells out to
# blastn/makeblastdb, which must resolve from the env — not system tools.
if [[ -d "${ENV_BIN}" ]]; then export PATH="${ENV_BIN}:${PATH}"; fi
log "pip install backend requirements into ${ENV_DESC}"
run "${PYTHON}" -m pip install -r "${REPO_DIR}/backend/requirements.txt"

# ---------------------------------------------------------------------------
# 2. Verify GenoFLU toolchain + reference DB
# ---------------------------------------------------------------------------
if [[ ${SKIP_VERIFY} -eq 1 ]]; then
  warn "skipping GenoFLU verification (--skip-verify)"
else
  GENOFLU="${ENV_BIN}/genoflu.py"
  [[ -x "${GENOFLU}" ]] || GENOFLU="${ENV_BIN}/genoflu"
  if [[ -x "${GENOFLU}" ]]; then
    ok "genoflu: $("${GENOFLU}" -v 2>&1 | head -1)"
  else
    warn "genoflu not found in env — re-run after env build completes."
  fi
  command -v blastn >/dev/null 2>&1 && ok "blastn: $(blastn -version 2>&1 | head -1)" \
    || warn "blastn not on PATH — GenoFLU cannot align segments."
  command -v seqkit >/dev/null 2>&1 && ok "seqkit: $(seqkit version 2>&1 | head -1)" \
    || warn "seqkit not on PATH — input FASTA QC will be skipped at runtime."

  # The reference DB ships in the package at <genoflu>/../dependencies.
  if [[ -x "${GENOFLU}" ]]; then
    GENO_REAL="$(readlink -f "${GENOFLU}")"
    DEP_DIR="$(cd "$(dirname "${GENO_REAL}")/.." && pwd)/dependencies"
    if [[ -d "${DEP_DIR}/fastas" && -f "${DEP_DIR}/genotype_key.xlsx" ]]; then
      n_fastas=$(find "${DEP_DIR}/fastas" -maxdepth 1 -type f | wc -l | tr -d ' ')
      ok "reference DB present: ${DEP_DIR}  (${n_fastas} reference FASTAs, genotype_key.xlsx)"
    else
      warn "reference DB not found under ${DEP_DIR} — GenoFLU runs will fail until present."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 3. Frontend build
# ---------------------------------------------------------------------------
if [[ ${SKIP_FRONTEND} -eq 1 ]]; then
  warn "skipping frontend build (--skip-frontend)"
else
  log "building React frontend"
  pushd "${REPO_DIR}/frontend" >/dev/null
  if command -v npm >/dev/null 2>&1; then
    run npm ci || run npm install
    run npm run build
  elif [[ -x node_modules/.bin/vite ]]; then
    run node_modules/.bin/vite build
  else
    SIB="/srv/kapurlab/tools/kraken_id_parse_gui/frontend/node_modules"
    if [[ -d "${SIB}" && ! -e node_modules ]]; then
      run ln -s "${SIB}" node_modules
      run node_modules/.bin/vite build
    else
      warn "no npm and no node_modules — frontend not built. Install Node and re-run."
    fi
  fi
  popd >/dev/null
  [[ -f "${REPO_DIR}/frontend/dist/index.html" ]] && ok "frontend built: ${REPO_DIR}/frontend/dist/"
fi

log "Done. Register the OOD app (sudo deploy/register_ood_apps.sh) and launch a session."
echo "  Backend entry:  ${REPO_DIR}/backend/app/main.py (uvicorn app.main:app)"
echo "  Env python:     ${PYTHON}"
echo "  Update genotypes later with: deploy/update_genoflu.sh"
