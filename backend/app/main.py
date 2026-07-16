"""
GenoFLU GUI — FastAPI backend.

Serves the React SPA from frontend/dist/ and provides:
  /api/projects        — list shared + personal projects (FASTA browser)
  /api/projects/{n}/samples — list assembled FASTA files in project/download/
  /api/projects/{n}/{inputs,upload,link-local,sra/download} — populate a project
  /api/config          — get/set user config (DB path, identity threshold)
  /api/run             — start a genoflu_pipeline.py run on a FASTA
  /api/jobs            — list running/completed jobs
  /api/jobs/{id}/log   — SSE stream of the job log
  /api/projects/{n}/samples/{s}/geno-results — per-sample result files
  /api/projects/{n}/samples/{s}/geno-table   — parsed genotype + per-segment

This backend is a sibling of vsnp_gui, kraken_id_parse_gui and amr_plus_gui and
shares their project layout. All URLs are served from / (uvicorn is behind the
OOD rnode proxy — relative paths only).
"""

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import load_config, save_config
from .jobs import JobManager
# NOTE: SRA read download was replaced by FASTA-by-accession/BioSample download
# (backend/app/sra.py is retained for reference but no longer wired into a route).

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent          # /srv/kapurlab/tools/genoflu_gui
_BIN_DIR = _REPO_ROOT / "bin"
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"

# Shared project root
_SHARED_PROJECTS = Path("/srv/kapurlab/projects")

# Jobs log directory (inside repo so it survives across sessions)
_JOBS_DIR = _REPO_ROOT / "backend" / "jobs"

# This tool's per-sample output subdir within a project.
_TOOL_SUBDIR = "genoflu"

# Assembled-FASTA extensions GenoFLU accepts as input.
_FASTA_EXTS = (".fasta", ".fa", ".fna", ".fas")

# ---------------------------------------------------------------------------
# App & job manager
# ---------------------------------------------------------------------------
app = FastAPI(title="GenoFLU GUI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

job_manager = JobManager(_JOBS_DIR)


# ---------------------------------------------------------------------------
# Helpers — project listing
# ---------------------------------------------------------------------------
_SCOPE_SHARED = "shared"
_SCOPE_PERSONAL = "personal"


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime if p.is_dir() else 0
    except PermissionError:
        return 0


def _is_fasta(name: str) -> bool:
    return name.lower().endswith(_FASTA_EXTS)


def _count_project_fastas(project_dir: Path) -> int:
    """Count assembled-FASTA inputs available to GenoFLU in a project
    (download/ files plus sibling IRMA assemblies under irma/)."""
    try:
        return len(_list_fasta_samples(project_dir))
    except PermissionError:
        return -1


def _list_projects_from_root(root: Path, scope: str) -> List[Dict]:
    if not root.is_dir():
        return []
    projects = []
    try:
        entries = sorted(root.iterdir(), key=_safe_mtime, reverse=True)
    except PermissionError:
        return []
    for p in entries:
        try:
            if not p.is_dir() or p.name.startswith("."):
                continue
        except PermissionError:
            continue
        try:
            fasta_count = _count_project_fastas(p)
        except PermissionError:
            fasta_count = -1
        geno_runs = []
        geno_dir = p / _TOOL_SUBDIR
        try:
            if geno_dir.is_dir():
                geno_runs = [d.name for d in sorted(geno_dir.iterdir()) if d.is_dir()]
        except PermissionError:
            pass
        projects.append({
            "name": p.name,
            "path": str(p),
            "scope": scope,
            "fasta_count": fasta_count,
            "geno_runs": geno_runs,
        })
    return projects


def _get_project_dir(name: str) -> Optional[Path]:
    """Find a project dir in shared then personal roots."""
    if "/" in name or name.startswith("."):
        return None
    cfg = load_config()
    for root in [_SHARED_PROJECTS, Path(cfg.get("projects_root", ""))]:
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Project creation — uses the SAME on-disk skeleton the sibling GUIs create, so
# a project made here is immediately usable in vSNP/Kraken/AMR (and vice versa).
# We add the genoflu/ subdir up front so the sample browser and results
# endpoints have a stable layout.
# ---------------------------------------------------------------------------
_PROJECT_NAME_OK_CHARSET = re.compile(r"^[A-Za-z0-9._-]+$")


def _normalize_project_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Project name must be a string")
    cleaned = re.sub(r"\s+", "_", name.strip())
    if not cleaned:
        raise ValueError("Project name is empty")
    if cleaned.startswith("."):
        raise ValueError("Project name cannot start with '.'")
    if len(cleaned) > 100:
        raise ValueError("Project name too long (max 100 characters)")
    if not _PROJECT_NAME_OK_CHARSET.match(cleaned):
        bad = sorted(set(ch for ch in cleaned if not re.match(r"[A-Za-z0-9._-]", ch)))
        raise ValueError(
            f"Project name contains unsupported characters: {''.join(bad)!r}. "
            "Only letters, digits, _ - . are allowed (spaces become underscores)."
        )
    return cleaned


def _ensure_project_dirs(project_dir: Path) -> None:
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    (project_dir / _TOOL_SUBDIR).mkdir(parents=True, exist_ok=True)
    # vSNP-compatible layout so the project is shared cleanly between tools.
    (project_dir / "step1").mkdir(parents=True, exist_ok=True)
    (project_dir / "step2" / "vcf_source").mkdir(parents=True, exist_ok=True)
    (project_dir / f"{project_dir.name}_VCFs").mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _create_project(name: str, scope: str) -> Path:
    name = _normalize_project_name(name)
    cfg = load_config()
    root = _SHARED_PROJECTS if scope == _SCOPE_SHARED else Path(
        cfg.get("projects_root", "") or (Path.home() / "projects"))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Cannot create projects root {root}: {exc}")
    project_dir = root / name
    if project_dir.exists():
        raise ValueError(f"Project already exists: {name}")
    try:
        _ensure_project_dirs(project_dir)
    except PermissionError:
        raise ValueError(
            f"No permission to create a project under {root}. "
            "Shared projects require lab write access; create it as a personal "
            "project instead."
        )
    meta = {"name": name, "created_at": _now_iso(), "status": "created"}
    try:
        with open(project_dir / "project.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
    except OSError:
        pass
    return project_dir


def _fasta_stem(name: str) -> str:
    return re.sub(r"\.(fasta|fa|fna|fas)$", "", name, flags=re.IGNORECASE)


def _list_fasta_samples(project_dir: Path) -> List[Dict]:
    """Every assembled FASTA available to GenoFLU in a project:

      * flat files in download/  (uploaded, FASTA-downloaded, or linked), and
      * per-sample IRMA assemblies under irma/<sample>/  — the submission FASTA
        (8 re-headed segments) if present, else assembly.fasta.

    Listing the sibling IRMA outputs means a genome assembled by the IRMA tool
    in the *same* project is directly runnable here, without copying files."""
    samples: List[Dict] = []
    seen_paths = set()

    def _add(path: Path, source: str) -> None:
        rp = str(path)
        if rp in seen_paths:
            return
        seen_paths.add(rp)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        samples.append({
            "sample": _fasta_stem(path.name),
            "fasta": rp,
            "fasta_name": path.name,
            "fasta_size": size,
            "source": source,
        })

    # download/ — flat FASTA files
    download_dir = project_dir / "download"
    if download_dir.is_dir():
        try:
            for f in sorted(download_dir.iterdir()):
                if f.is_file() and not f.name.startswith(".") and _is_fasta(f.name):
                    _add(f, "download")
        except PermissionError:
            pass

    # irma/<sample>/ — sibling IRMA assemblies (prefer the submission FASTA)
    irma_dir = project_dir / "irma"
    if irma_dir.is_dir():
        try:
            for run in sorted(irma_dir.iterdir()):
                if not run.is_dir() or run.name.startswith("."):
                    continue
                sub = run / f"{run.name}-submission.fasta"
                asm = run / "assembly.fasta"
                if sub.is_file():
                    _add(sub, "irma")
                elif asm.is_file():
                    _add(asm, "irma")
                else:
                    subs = sorted(run.glob("*-submission.fasta")) or sorted(run.glob("*.fasta"))
                    if subs:
                        _add(subs[0], "irma")
        except PermissionError:
            pass

    return samples


# ---------------------------------------------------------------------------
# API routes — projects
# ---------------------------------------------------------------------------
@app.get("/api/projects")
def api_list_projects():
    cfg = load_config()
    projects = _list_projects_from_root(_SHARED_PROJECTS, _SCOPE_SHARED)
    personal_root = Path(cfg.get("projects_root", ""))
    if personal_root != _SHARED_PROJECTS:
        personal = _list_projects_from_root(personal_root, _SCOPE_PERSONAL)
        seen = {p["name"] for p in projects}
        projects += [p for p in personal if p["name"] not in seen]
    return JSONResponse(projects)


class ProjectCreate(BaseModel):
    name: str
    scope: Optional[str] = None


@app.post("/api/projects")
def api_create_project(payload: ProjectCreate):
    scope = (payload.scope or _SCOPE_PERSONAL).strip() or _SCOPE_PERSONAL
    if scope not in (_SCOPE_PERSONAL, _SCOPE_SHARED):
        raise HTTPException(400, f"Invalid scope: {scope!r}")
    try:
        project_dir = _create_project(payload.name, scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse({"name": project_dir.name, "path": str(project_dir), "scope": scope})


# ---------------------------------------------------------------------------
# Loading samples into a project — import (link), upload, SRA download.
# All land files in <project>/download/.
# ---------------------------------------------------------------------------
def _writable_project_dir(name: str) -> Path:
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    return project_dir


@app.get("/api/projects/{name}/inputs")
def api_project_inputs(name: str):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    download_dir = project_dir / "download"
    files: List[Dict] = []
    total = 0
    if download_dir.is_dir():
        for p in sorted(download_dir.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            files.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime,
                          "is_fasta": _is_fasta(p.name)})
            total += st.st_size
    return JSONResponse({"files": files, "total_bytes": total, "count": len(files)})


@app.delete("/api/projects/{name}/inputs/{filename}")
def api_project_input_delete(name: str, filename: str):
    if not filename or "/" in filename or "\\" in filename or filename.startswith(".") or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    target = project_dir / "download" / filename
    if not target.is_file() and not target.is_symlink():
        raise HTTPException(404, f"File not found: {filename}")
    target.unlink()
    return JSONResponse({"deleted": filename})


@app.post("/api/projects/{name}/upload")
async def api_project_upload(name: str, files: List[UploadFile] = File(...)):
    """Save drag-and-dropped / chosen FASTA files into <project>/download/."""
    project_dir = _writable_project_dir(name)
    download_dir = project_dir / "download"
    saved = 0
    for f in files:
        if not f.filename:
            continue
        target = download_dir / Path(f.filename).name
        async with aiofiles.open(target, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                await out.write(chunk)
        saved += 1
    return JSONResponse({"uploaded": saved})


class LinkLocalRequest(BaseModel):
    path: str


@app.post("/api/projects/{name}/link-local")
def api_project_link_local(name: str, payload: LinkLocalRequest):
    """Symlink every assembled FASTA (or *.fastq.gz, for cross-tool projects)
    under a server-side directory into download/ — no copying."""
    project_dir = _writable_project_dir(name)
    src = Path((payload.path or "").strip()).expanduser()
    if not src.exists():
        raise HTTPException(400, f"Input path not found: {src}")
    download_dir = project_dir / "download"
    _accept = _FASTA_EXTS + (".fastq.gz",)
    if src.is_file():
        candidates = [src]
    else:
        candidates = sorted(f for f in src.iterdir()
                            if f.is_file() and f.name.lower().endswith(_accept))
    count = 0
    for f in candidates:
        if not f.name.lower().endswith(_accept):
            continue
        target = download_dir / f.name
        if not target.exists():
            target.symlink_to(f.resolve())
            count += 1
    return JSONResponse({"linked": count})


class FastaDownloadRequest(BaseModel):
    accessions: List[str]
    rename: bool = True      # save metadata-derived names (organism/strain) vs bare accession


@app.post("/api/projects/{name}/fasta/download")
def api_project_fasta_download(name: str, payload: FastaDownloadRequest):
    """Download genome FASTAs by accession / BioSample into download/ as a
    background job. GenoFLU genotypes an assembled influenza genome, so this
    (not SRA reads) is the primary input path.

    GCA/GCF assemblies go through the NCBI `datasets` CLI; nucleotide accessions
    through eutils efetch; a BioSample (SAMN…) or sample name is resolved to its
    linked nucleotide records and concatenated into one multi-FASTA (the 8 flu
    segments as a single genome)."""
    project_dir = _writable_project_dir(name)
    accs = [a.strip() for a in (payload.accessions or []) if a.strip()]
    if not accs:
        raise HTTPException(400, "No accessions provided.")
    download_dir = project_dir / "download"
    download_dir.mkdir(parents=True, exist_ok=True)
    script = _BIN_DIR / "download_fasta.py"
    command = [sys.executable, "-u", str(script), "--outdir", str(download_dir)]
    if not payload.rename:
        command.append("--no-rename")
    command += ["--accessions", *accs]
    env = {
        "PYTHONPATH": str(_BIN_DIR),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
    }
    job_id = job_manager.start_job(
        name=f"fasta_download — {name} ({len(accs)})",
        command=command, cwd=download_dir, env=env,
    )
    return JSONResponse({"job_id": job_id, "count": len(accs)})


@app.get("/api/projects/{name}/samples")
def api_project_samples(name: str):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    return JSONResponse(_list_fasta_samples(project_dir))


# ---------------------------------------------------------------------------
# Per-sample GenoFLU results (read straight from <project>/genoflu/<sample>/ so
# any previously-run sample is revisitable, not just the last job).
# ---------------------------------------------------------------------------
def _sample_run_status(run_dir: Path) -> str:
    run_dir_str = str(run_dir)
    for job in job_manager.list_jobs():
        if job.get("cwd") == run_dir_str and job.get("status") == "running":
            return "running"
    try:
        if run_dir.is_dir() and any(p.is_file() for p in run_dir.rglob("*")):
            return "done"
    except PermissionError:
        pass
    return "none"


def _collect_result_files(run_dir: Path, include_all: bool) -> List[Dict]:
    files: List[Dict] = []
    if not run_dir.is_dir():
        return files
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name.endswith(".log"):
            continue
        rel = str(p.relative_to(run_dir))
        category = _result_category(rel)
        if not include_all and category is None:
            continue
        stat = p.stat()
        files.append({
            "name": rel,
            "path": str(p),
            "label": _result_label(rel, category),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "openable": _can_open_inline(rel),
            "category": category,
        })

    def sort_key(f):
        category = f.get("category")
        if category in _CATEGORY_ORDER:
            return (_CATEGORY_ORDER[category], f["name"])
        return (50, f["name"])

    files.sort(key=sort_key)
    for f in files:
        f.pop("mtime", None)
        if include_all and f.get("category") is None:
            f["label"] = f["name"]
    return files


@app.get("/api/projects/{name}/samples/{sample}/geno-results")
def api_sample_geno_results(name: str, sample: str, all: int = Query(0)):
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    run_dir = project_dir / _TOOL_SUBDIR / sample
    return JSONResponse({
        "project": name,
        "sample": sample,
        "present": run_dir.is_dir(),
        "status": _sample_run_status(run_dir),
        "run_dir": str(run_dir),
        "files": _collect_result_files(run_dir, bool(all)),
    })


@app.get("/api/projects/{name}/samples/{sample}/geno-table")
def api_sample_geno_table(name: str, sample: str):
    """Return the parsed genotype + per-segment results and provenance for one
    sample, from genoflu_result.json / run_manifest.json."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    run_dir = project_dir / _TOOL_SUBDIR / sample
    result = _load_json_file(run_dir / "genoflu_result.json")
    qc = _load_json_file(run_dir / "fasta_qc.json")
    provenance = _load_json_file(run_dir / "run_manifest.json")
    present = (run_dir / "genoflu_result.json").is_file()
    return JSONResponse({
        "project": name,
        "sample": sample,
        "present": present,
        "result": result,
        "qc": qc,
        "provenance": provenance,
    })


def _load_json_file(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


@app.get("/api/projects/{name}/file")
def api_project_file(name: str, path: str = Query(...), inline: int = 0):
    """Serve a file from anywhere inside a project dir."""
    project_dir = _get_project_dir(name)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {name}")
    root = project_dir.resolve()
    target = Path(path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(403, "Path outside project directory")
    if not target.is_file():
        raise HTTPException(404, f"File not found: {path}")
    media_type = _media_type_for(target.name)
    want_inline = bool(inline) and _can_open_inline(target.name)
    disposition = "inline" if want_inline else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{target.name}"'}
    return FileResponse(target, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@app.get("/api/config")
def api_get_config():
    return JSONResponse(load_config())


class ConfigPayload(BaseModel):
    genoflu_db: Optional[str] = None
    pident_threshold: Optional[float] = None
    projects_root: Optional[str] = None
    shared_projects_root: Optional[str] = None
    saved_project_roots: Optional[List[str]] = None


@app.post("/api/config")
def api_save_config(payload: ConfigPayload):
    cfg = load_config()
    updates = payload.model_dump(exclude_none=True)
    cfg.update(updates)
    roots = cfg.get("saved_project_roots") or []
    if isinstance(roots, list):
        seen, cleaned = set(), []
        for r in roots:
            r = (r or "").strip()
            if r and r not in seen:
                seen.add(r)
                cleaned.append(r)
        cfg["saved_project_roots"] = cleaned
    save_config(cfg)
    return JSONResponse({"ok": True})


@app.get("/api/browse-dirs")
def api_browse_dirs(path: str = ""):
    try:
        p = (Path(path).expanduser() if path.strip() else Path.home()).resolve()
    except (OSError, RuntimeError):
        raise HTTPException(400, "Invalid path")
    if not p.is_dir():
        raise HTTPException(400, f"Not a directory: {p}")
    entries: List[Dict[str, str]] = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {p}")
    parent = str(p.parent) if p.parent != p else None
    return JSONResponse({"path": str(p), "parent": parent, "entries": entries})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
class RunPayload(BaseModel):
    project: str
    fasta: str                          # absolute path to an assembled FASTA
    pident: Optional[float] = None
    genoflu_db: Optional[str] = None


@app.post("/api/run")
def api_run(payload: RunPayload):
    cfg = load_config()
    pident = payload.pident if payload.pident is not None else cfg.get("pident_threshold", 98.0)
    genoflu_db = payload.genoflu_db or cfg.get("genoflu_db", "")

    project_dir = _get_project_dir(payload.project)
    if project_dir is None:
        raise HTTPException(404, f"Project not found: {payload.project}")

    fasta = Path(payload.fasta)
    if not fasta.exists():
        raise HTTPException(400, f"Input FASTA not found: {payload.fasta}")
    if not _is_fasta(fasta.name):
        raise HTTPException(400, f"Not a FASTA file: {fasta.name}")

    sample_name = re.sub(r"\.(fasta|fa|fna|fas)$", "", fasta.name, flags=re.IGNORECASE)
    run_dir = project_dir / _TOOL_SUBDIR / sample_name

    for existing in job_manager.list_jobs():
        if existing.get("status") == "running" and existing.get("cwd") == str(run_dir):
            raise HTTPException(
                409,
                f"A run is already in progress for {sample_name} "
                f"(job {existing['id'][:8]}). Wait for it to finish before re-running.",
            )

    run_dir.mkdir(parents=True, exist_ok=True)

    script = _BIN_DIR / "genoflu_pipeline.py"
    command = [sys.executable, "-u", str(script),
               "--sample", sample_name,
               "--outdir", str(run_dir),
               "--fasta", str(fasta),
               "--pident", str(pident)]
    if genoflu_db:
        command.extend(["--genoflu-db", genoflu_db])

    env = {
        "PYTHONPATH": str(_BIN_DIR),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }

    job_name = f"{payload.project}/{sample_name} — GenoFLU (>= {pident}%)"
    job_id = job_manager.start_job(name=job_name, command=command, cwd=run_dir, env=env)
    return JSONResponse({"job_id": job_id, "run_dir": str(run_dir)})


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@app.get("/api/jobs")
def api_list_jobs():
    return JSONResponse(job_manager.list_jobs())


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return JSONResponse(job)


@app.get("/api/jobs/{job_id}/log")
async def api_job_log(job_id: str, request: Request):
    """SSE stream of the job's log file. Closes when the job finishes."""
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    log_path = Path(job["log_path"])
    _ansi_re = re.compile(r'\x1b\[[0-9;]*[mGKHFABCDJsur]')

    async def event_stream():
        position = 0
        while True:
            if await request.is_disconnected():
                break
            current_job = job_manager.get_job(job_id)
            if log_path.exists():
                async with aiofiles.open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    await f.seek(position)
                    chunk = await f.read(4096)
                    if chunk:
                        lines = chunk.splitlines(keepends=True)
                        for line in lines:
                            clean = _ansi_re.sub("", line.rstrip())
                            if clean:
                                yield f"data: {clean}\n\n"
                        position += len(chunk.encode("utf-8"))
            if current_job and current_job["status"] in ("succeeded", "failed"):
                yield "data: [DONE]\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Result file media handling + categorization
# ---------------------------------------------------------------------------
_INLINE_MEDIA = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".json": "application/json",
    ".tsv": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".csv": "text/plain",
}
_DOWNLOAD_MEDIA = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".fasta": "text/plain",
    ".fa": "text/plain",
    ".fna": "text/plain",
    ".gz": "application/gzip",
}


def _can_open_inline(name: str) -> bool:
    return Path(name).suffix.lower() in _INLINE_MEDIA


def _media_type_for(name: str) -> str:
    ext = Path(name).suffix.lower()
    return _INLINE_MEDIA.get(ext) or _DOWNLOAD_MEDIA.get(ext) or "application/octet-stream"


def _result_category(rel: str) -> Optional[str]:
    path = Path(rel)
    name = path.name
    parts = path.parts
    if any(part.startswith(".") for part in parts):
        return None
    if name == "report.pdf":
        return "report_pdf"
    if name.endswith("_stats.xlsx"):
        return "stats_xlsx"
    if name == "genoflu_genotype.tsv":
        return "genoflu_tsv"
    if name == "genoflu_genotype.xlsx":
        return "genoflu_xlsx"
    if name == "genoflu_result.json":
        return "genoflu_result"
    if name == "fasta_qc.json":
        return "fasta_qc"
    if name == "run_manifest.json":
        return "run_manifest"
    if name == "pipeline.log":
        return "log"
    return None


_CATEGORY_ORDER = {
    "report_pdf": 0,
    "stats_xlsx": 1,
    "genoflu_tsv": 2,
    "genoflu_xlsx": 3,
    "genoflu_result": 4,
    "fasta_qc": 5,
    "run_manifest": 6,
    "log": 99,
}


def _result_label(rel: str, category: Optional[str]) -> str:
    return {
        "report_pdf": "Report (PDF)",
        "stats_xlsx": "Statistics workbook (Excel, single column)",
        "genoflu_tsv": "GenoFLU genotype (TSV)",
        "genoflu_xlsx": "GenoFLU genotype (Excel)",
        "genoflu_result": "Parsed result (JSON)",
        "fasta_qc": "Input FASTA QC (JSON)",
        "run_manifest": "Run manifest / provenance (JSON)",
        "log": "Pipeline log",
    }.get(category, rel)


@app.get("/api/jobs/{job_id}/results")
def api_job_results(job_id: str, all: int = Query(0)):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    files = []
    cwd = job.get("cwd")
    if cwd and Path(cwd).is_dir():
        run_dir = Path(cwd)
        for p in sorted(run_dir.rglob("*")):
            if p.is_file() and not p.name.endswith(".log"):
                rel = str(p.relative_to(run_dir))
                category = _result_category(rel)
                if not all and category is None:
                    continue
                files.append({
                    "name": rel,
                    "label": _result_label(rel, category),
                    "size": p.stat().st_size,
                    "mtime": p.stat().st_mtime,
                    "openable": _can_open_inline(rel),
                    "category": category,
                })
    log_path = Path(job.get("log_path", ""))
    if log_path.is_file():
        files.append({
            "name": "pipeline_log.txt",
            "label": "Pipeline log",
            "size": log_path.stat().st_size,
            "mtime": log_path.stat().st_mtime,
            "openable": True,
            "category": "log",
            "is_log": True,
        })

    def sort_key(f):
        if f.get("is_log"):
            return (_CATEGORY_ORDER["log"], f["name"])
        category = f.get("category")
        if category in _CATEGORY_ORDER:
            return (_CATEGORY_ORDER[category], f["name"])
        return (50, f["name"])

    files.sort(key=sort_key)
    for file in files:
        file.pop("mtime", None)
        if all and file.get("category") is None:
            file["label"] = file["name"]
    return JSONResponse(files)


@app.get("/api/jobs/{job_id}/file")
def api_job_file(job_id: str, path: str = Query(...), inline: int = 0):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if path == "pipeline_log.txt":
        target = Path(job.get("log_path", ""))
        display_name = f"{job_id[:8]}_pipeline_log.txt"
    else:
        cwd = job.get("cwd")
        if not cwd:
            raise HTTPException(404, "No run directory for job")
        run_dir = Path(cwd).resolve()
        target = (run_dir / path).resolve()
        if run_dir != target and run_dir not in target.parents:
            raise HTTPException(403, "Path outside run directory")
        display_name = target.name
    if not target.is_file():
        raise HTTPException(404, f"File not found: {path}")
    media_type = _media_type_for(target.name)
    want_inline = bool(inline) and _can_open_inline(target.name)
    disposition = "inline" if want_inline else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{display_name}"'}
    return FileResponse(target, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Static frontend — must be last (catches everything not matched above)
# ---------------------------------------------------------------------------
if _FRONTEND_DIST.is_dir():
    _INDEX_HTML = _FRONTEND_DIST / "index.html"

    @app.get("/")
    def index():
        return FileResponse(
            _INDEX_HTML,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="static")
else:
    @app.get("/")
    def root():
        return JSONResponse(
            {"error": "Frontend not built. Run: cd frontend && npm run build"},
            status_code=503,
        )
