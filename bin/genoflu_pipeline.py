#!/usr/bin/env python
"""
genoflu_pipeline.py — orchestrator for the GenoFLU GUI.

Pipeline (per sample):
  1. Input-file QC — seqkit stats / fx2tab on the assembled FASTA -> fasta_qc.json
     (segment count, per-segment length + GC, total length, N50). For a clean
     influenza-A genome this should be 8 segments; the QC flags otherwise.
  2. GenoFLU — run_genoflu.py BLASTs the 8 segments against the reference set and
     assigns a genotype, capturing every option + provenance (run_manifest.json,
     genoflu_result.json, genoflu_genotype.{tsv,xlsx}).
  3. Report — reporting.build() writes a single-labeled-column stats workbook
     (<sample>_<date>_stats.xlsx, vSNP3-style) and a human-readable report.pdf.

Output dir is <project>/genoflu/<sample>/ (passed via --outdir). Every artifact
lands there with a stable name so the backend's result endpoints find it.

Usage:
  genoflu_pipeline.py --sample S --outdir DIR --fasta GENOME.fasta
      [--pident 98.0] [--genoflu-db /path/to/dependencies]
"""

# --- provenance: log every external command this pipeline runs (best-effort) ---
# Attribute-level wrap of subprocess.Popen (which run/call/check_* all funnel
# through) + os.system, so EVERY external tool command (kraken2, amrfinder,
# blastn, spades, raxml, …) is recorded once to
# <outdir>/.provenance/<tool>_commands.txt — the exact commands that produced the
# results in this folder. Never alters behaviour; logging failures are swallowed
# and the original call always runs, so it can't break the pipeline.
def _install_provenance_capture():
    import os as _o, subprocess as _s, shlex as _sh
    from pathlib import Path as _P
    from datetime import datetime as _dt
    _tool = _P(__file__).resolve().parents[1].name
    _out = _P.cwd() / ".provenance"
    _f = _out / (_tool + "_commands.txt")
    def _log(_cmd):
        try:
            _out.mkdir(parents=True, exist_ok=True)
            _ln = _cmd if isinstance(_cmd, str) else _sh.join(str(c) for c in _cmd)
            _ts = _dt.now().astimezone().strftime("%H:%M:%S")
            with open(_f, "a", encoding="utf-8") as _h:
                _h.write(_ts + "  " + _ln + "\n")
        except Exception:
            pass
    try:
        _out.mkdir(parents=True, exist_ok=True)
        with open(_f, "a", encoding="utf-8") as _h:
            _h.write("\n# === %s run %s — external commands that produced results in this folder ===\n"
                     % (_tool, _dt.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")))
    except Exception:
        pass
    _orig_popen = _s.Popen
    class _Popen(_orig_popen):
        def __init__(self, args, *a, **k):
            _log(args)
            super().__init__(args, *a, **k)
    _s.Popen = _Popen
    _osys = _o.system
    def _sysw(_cmd):
        _log(_cmd)
        return _osys(_cmd)
    _o.system = _sysw
try:
    _install_provenance_capture()
except Exception:
    pass
# --- end provenance ------------------------------------------------------------


import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import run_genoflu  # local import (PYTHONPATH includes bin/)


def log(msg: str) -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    log("")
    log(f"### {title}")


def _have(tool: str) -> bool:
    import shutil
    return shutil.which(tool) is not None


# ---------------------------------------------------------------------------
# Step 1 — input FASTA QC (the "quality stats of the input files")
# ---------------------------------------------------------------------------
def fasta_qc(fasta: Path, outdir: Path) -> Dict[str, Any]:
    """seqkit stats + fx2tab on the assembled FASTA -> fasta_qc.json.

    Influenza A has 8 segments; an analysis-ready genome should contain all 8.
    We record the aggregate stats and a per-segment length/GC breakdown, and a
    pass/review verdict based on segment count, so the report can show input
    quality and flag incomplete genomes before interpreting a genotype call."""
    qc: Dict[str, Any] = {
        "file": fasta.name, "verdict": "review", "metrics": {},
        "segments": [], "expected_segments": 8, "notes": [],
    }
    if not _have("seqkit"):
        qc["notes"].append("seqkit not on PATH — input FASTA QC unavailable.")
        _write(outdir / "fasta_qc.json", qc)
        return qc

    # Aggregate stats.
    try:
        proc = subprocess.run(["seqkit", "stats", "-T", "-a", str(fasta)],
                              capture_output=True, text=True, timeout=300)
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if len(lines) >= 2:
            row = dict(zip(lines[0].split("\t"), lines[1].split("\t")))

            def num(k):
                try:
                    return float(str(row.get(k, "")).replace(",", ""))
                except (ValueError, AttributeError):
                    return None

            qc["metrics"] = {
                "num_seqs": num("num_seqs"),
                "total_length": num("sum_len"),
                "min_len": num("min_len"),
                "avg_len": num("avg_len"),
                "max_len": num("max_len"),
                "n50": num("N50"),
                "gc_pct": num("GC(%)"),
            }
    except (subprocess.SubprocessError, OSError) as exc:
        qc["notes"].append(f"seqkit stats failed: {exc}")

    # Per-segment length + GC.
    try:
        proc = subprocess.run(
            ["seqkit", "fx2tab", "--name", "--only-id", "--length", "--gc", str(fasta)],
            capture_output=True, text=True, timeout=300)
        for ln in (proc.stdout or "").splitlines():
            parts = ln.split("\t")
            if len(parts) >= 3:
                try:
                    qc["segments"].append({
                        "name": parts[0],
                        "length": int(float(parts[1])),
                        "gc_pct": float(parts[2]),
                    })
                except ValueError:
                    continue
    except (subprocess.SubprocessError, OSError) as exc:
        qc["notes"].append(f"seqkit fx2tab failed: {exc}")

    n = (qc["metrics"] or {}).get("num_seqs")
    if n is not None:
        if int(n) == 8:
            qc["verdict"] = "pass"
        else:
            qc["verdict"] = "review"
            qc["notes"].append(
                f"{int(n)} sequence(s) present; influenza A has 8 segments. "
                "A genotype can only be assigned when all 8 segments are present "
                "and pass the identity threshold.")
    _write(outdir / "fasta_qc.json", qc)
    return qc


def _write(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GenoFLU pipeline orchestrator.")
    ap.add_argument("--sample", required=True)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--fasta", type=Path, required=True)
    ap.add_argument("--pident", type=float, default=98.0)
    ap.add_argument("--genoflu-db", default=None)
    args = ap.parse_args(argv)

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    log("=" * 70)
    log(f"GenoFLU pipeline — sample: {args.sample}")
    log(f"  input:   {args.fasta}")
    log(f"  outdir:  {outdir}")
    log(f"  pident:  {args.pident}%")
    log("=" * 70)

    if not args.fasta.exists():
        log(f"ERROR: input FASTA not found: {args.fasta}")
        return 2

    # ---- Step 1: input FASTA QC ----
    step("Step 1: Input file QC (seqkit on the assembled FASTA)")
    qc = fasta_qc(args.fasta, outdir)
    m = qc.get("metrics") or {}
    log(f"  {int(m.get('num_seqs') or 0)} sequence(s), total {int(m.get('total_length') or 0):,} bp, "
        f"GC {m.get('gc_pct')}%  -> QC verdict: {qc.get('verdict')}")
    for note in qc.get("notes", []):
        log(f"  - {note}")

    # ---- Step 2: GenoFLU genotyping ----
    step("Step 2: GenoFLU genotyping (BLASTN of 8 segments)")
    manifest = run_genoflu.run(
        fasta=args.fasta,
        outdir=outdir,
        name=args.sample,
        pident=args.pident,
        genoflu_db=args.genoflu_db,
        extra_provenance={
            "pipeline_started_at": started,
            "pipeline_finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "input_qc": qc,
            "versions_extra": {
                "seqkit": run_genoflu._tool_version(["seqkit", "version"]),
            },
        },
    )
    rc = manifest.get("return_code", 1)

    # ---- Step 3: Report (stats workbook + PDF) ----
    step("Step 3: Building report (stats.xlsx + report.pdf)")
    try:
        import reporting  # bin/ is on PYTHONPATH
        reporting.build(outdir, args.sample, log=log)
    except Exception as exc:  # noqa: BLE001 — never fail the run over the report
        log(f"  WARNING: report generation failed: {exc}")

    step("Pipeline completed")
    log(f"GenoFLU return code: {rc}")
    log(f"Genotype: {manifest.get('genotype') or '(not assigned)'}")
    log(f"Outputs in: {outdir}")
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    sys.exit(main())
