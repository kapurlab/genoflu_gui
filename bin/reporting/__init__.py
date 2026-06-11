"""
GenoFLU GUI — report builder.

Produces two deliverables from a completed per-sample run directory:

  <sample>_<date>_stats.xlsx
      A single labeled column of statistics (column A = label, column B =
      value), modelled on the vSNP3 stats workbook so the tools read the same
      way. Input-file QC, the genotype call, and per-segment match metrics in
      one flat, labeled list. (GenoFLU's own wide native workbook is kept too,
      as genoflu_genotype.xlsx.)

  report.pdf
      A human-readable PDF: input file quality, a plain-language analysis
      summary (with a per-segment %identity figure when matplotlib is
      available), the genotype call and per-segment results table, and a
      methods/provenance page with the standards referenced and a disclaimer.

Both are best-effort: a missing artifact or a missing optional dependency
(reportlab / matplotlib) degrades gracefully and is reported in the log rather
than failing the pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fmt_int(v: Any) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _fmt_pct(v: Any, dp: int = 2) -> str:
    try:
        return f"{float(v):.{dp}f}%"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


# ---------------------------------------------------------------------------
# Build the ordered, labeled stats list (one metric per row)
# ---------------------------------------------------------------------------
def build_stats_items(
    sample: str,
    date_stamp: str,
    qc: Dict[str, Any],
    result: Dict[str, Any],
    manifest: Dict[str, Any],
) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    opts = manifest.get("options", {}) or {}
    vers = manifest.get("versions", {}) or {}

    # — Sample —
    items.append(("sample", sample))
    items.append(("date", date_stamp))
    items.append(("Pipeline", manifest.get("tool", "GenoFLU")))

    # — Input file quality —
    m = (qc or {}).get("metrics", {}) or {}
    items.append(("Input FASTA", qc.get("file", "—")))
    items.append(("Segments present", _fmt_int(m.get("num_seqs"))))
    items.append(("Expected segments", _fmt_int(qc.get("expected_segments", 8))))
    items.append(("Total length (bp)", _fmt_int(m.get("total_length"))))
    items.append(("Shortest segment (bp)", _fmt_int(m.get("min_len"))))
    items.append(("Longest segment (bp)", _fmt_int(m.get("max_len"))))
    items.append(("N50 (bp)", _fmt_int(m.get("n50"))))
    items.append(("GC (%)", _fmt_pct(m.get("gc_pct"))))
    items.append(("Input QC verdict", (qc.get("verdict") or "—").upper()))

    # — Genotype call —
    items.append(("Genotype", result.get("genotype") or "—"))
    items.append(("Genotype assigned", "yes" if result.get("complete") else "no"))
    items.append(("Segments matched (>= threshold)", _fmt_int(result.get("segments_matched"))))
    items.append(("Percent-identity threshold (%)", str(opts.get("pident_threshold", "—"))))

    # — Per-segment metrics (vSNP3-style flat labels) —
    for seg in result.get("segments", []) or []:
        g = (seg.get("segment") or "seg").upper()
        items.append((f"{g} lineage", seg.get("lineage") or "—"))
        items.append((f"{g} % identity", seg.get("percent_identity") or "—"))
        items.append((f"{g} mismatches", seg.get("mismatches") or "—"))
        items.append((f"{g} avg depth", seg.get("avg_depth") or "—"))
        if seg.get("reference"):
            items.append((f"{g} top reference", seg.get("reference")))

    # — Methods / provenance —
    items.append(("GenoFLU version", vers.get("genoflu") or "—"))
    items.append(("BLASTN version", vers.get("blastn") or "—"))
    items.append(("Reference DB", vers.get("reference_db") or "—"))
    items.append(("Reference DB source", opts.get("genoflu_db") or "—"))
    iso = [r.get("standard") for r in (manifest.get("iso_references") or []) if r.get("standard")]
    items.append(("Standards referenced", ", ".join(iso) if iso else "—"))
    return items


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build(outdir: Path, sample: str, log=print) -> Dict[str, Optional[str]]:
    """Build stats.xlsx + report.pdf for a finished run dir. Returns the paths
    (or None for any artifact that couldn't be produced). Never raises."""
    outdir = Path(outdir)
    result_paths: Dict[str, Optional[str]] = {"stats_xlsx": None, "report_pdf": None}

    qc = _load_json(outdir / "fasta_qc.json")
    result = _load_json(outdir / "genoflu_result.json")
    manifest = _load_json(outdir / "run_manifest.json")

    date_stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    items = build_stats_items(sample, date_stamp, qc, result, manifest)

    # --- stats workbook (single labeled column) ---
    try:
        from .stats_excel import write_stats_xlsx
        xlsx_path = outdir / f"{sample}_{date_stamp}_stats.xlsx"
        write_stats_xlsx(items, xlsx_path, sample)
        result_paths["stats_xlsx"] = str(xlsx_path)
        log(f"  wrote {xlsx_path.name}")
    except Exception as exc:  # noqa: BLE001 — soft-fail, report it
        log(f"  WARNING: stats workbook not written: {exc}")

    # --- PDF report ---
    try:
        from .pdf_report import write_pdf
        pdf_path = outdir / "report.pdf"
        ctx = {
            "sample": sample,
            "date": date_stamp,
            "qc": qc,
            "result": result,
            "manifest": manifest,
            "stats_items": items,
        }
        write_pdf(ctx, pdf_path, outdir)
        result_paths["report_pdf"] = str(pdf_path)
        log(f"  wrote {pdf_path.name}")
    except Exception as exc:  # noqa: BLE001
        log(f"  WARNING: PDF report not written ({exc}). Is reportlab installed?")

    return result_paths


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build GenoFLU stats.xlsx + report.pdf for a run dir.")
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--sample", required=True)
    args = ap.parse_args()
    out = build(args.outdir, args.sample)
    print(json.dumps(out, indent=2))
