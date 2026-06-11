import json
import os
from pathlib import Path
from typing import Any, Dict


def _user_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / "genoflu_gui"
    return Path.home() / ".config" / "genoflu_gui"


DATA_DIR = _user_config_dir()
CONFIG_PATH = DATA_DIR / "config.json"

_SHARED_PROJECTS_ROOT = Path("/srv/kapurlab/projects")
_DEFAULT_SHARED_PROJECTS_ROOT = (
    str(_SHARED_PROJECTS_ROOT) if _SHARED_PROJECTS_ROOT.is_dir() else ""
)


def _first_existing(*paths: str) -> str:
    """Return the first path that exists, else the first candidate (so the
    default is informative even on a fresh box)."""
    for p in paths:
        if p and Path(p).exists():
            return p
    return paths[0] if paths else ""


# GenoFLU reference database directory — the `dependencies/` dir that ships in
# the conda package, holding `fastas/` (the BLAST reference segments) and
# `genotype_key.xlsx` (the per-segment lineage -> genotype key). Empty by
# default: the runner resolves it relative to the installed genoflu.py, which is
# the reproducible, version-pinned location whose version is recorded in each
# run's provenance. Set this only to point GenoFLU at an out-of-tree reference
# set (e.g. a GitHub checkout for pre-release genotypes).
_GENOFLU_DB_DEFAULT = _first_existing(
    "/srv/kapurlab/databases/genoflu/dependencies",
    "",
)

DEFAULTS: Dict[str, Any] = {
    "projects_root": str(Path.home() / "projects"),
    "shared_projects_root": _DEFAULT_SHARED_PROJECTS_ROOT,
    # Path to a GenoFLU `dependencies/` dir; blank = use the conda package's.
    "genoflu_db": _GENOFLU_DB_DEFAULT,
    # BLAST percent-identity threshold for calling a segment a match. 98.0 is
    # GenoFLU's default and the value used in USDA-VS surveillance reporting.
    "pident_threshold": 98.0,
}


def load_config() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
