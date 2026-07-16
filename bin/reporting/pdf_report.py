"""
GenoFLU PDF report (reportlab + matplotlib).

Pure-Python PDF — no headless browser — so it renders reliably on any OOD host.
matplotlib figures are best-effort: if matplotlib is unavailable the report is
still produced, just without the charts.

Layout: title + genotype banner, a plain-language analysis summary, input-file
quality (per-segment table), the genotype call with a per-segment results table
(and a %identity figure), and a methods/provenance page with the standards
referenced and an interpretation disclaimer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Theme (matches the GUI's App.css palette)
TEAL = colors.HexColor("#4C8C8A")
TERRA = colors.HexColor("#C88F7A")
INK = colors.HexColor("#1F2A2E")
MUTED = colors.HexColor("#6E7B82")
PANEL = colors.HexColor("#F1EDE6")
BORDER = colors.HexColor("#E3DED6")
DANGER = colors.HexColor("#C46A6A")
SUCCESS = colors.HexColor("#6BAA75")
WARN = colors.HexColor("#D8B26E")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1", parent=ss["Title"], textColor=INK, fontSize=20, spaceAfter=2))
    ss.add(ParagraphStyle("Sub", parent=ss["Normal"], textColor=MUTED, fontSize=10, spaceAfter=10))
    ss.add(ParagraphStyle("H2", parent=ss["Heading2"], textColor=TEAL, fontSize=13,
                          spaceBefore=12, spaceAfter=4))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], textColor=INK, fontSize=9.5,
                          leading=13, alignment=TA_LEFT, spaceAfter=4))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], textColor=MUTED, fontSize=8, leading=10))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], textColor=INK, fontSize=8.5, leading=11))
    return ss


def _kv_table(rows: List[Tuple[str, str]], ss, col0=2.4 * inch, col1=4.4 * inch) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", ss["Cell"]), Paragraph(str(v), ss["Cell"])] for k, v in rows]
    t = Table(data, colWidths=[col0, col1])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#FBFAF8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _banner(text: str, fill, ss) -> Table:
    t = Table([[Paragraph(f'<font color="white"><b>{text}</b></font>', ss["Body"])]],
              colWidths=[6.9 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


# ---------------------------------------------------------------------------
# Figure (best-effort): per-segment % identity
# ---------------------------------------------------------------------------
def _bar_identity(segments: List[Dict[str, Any]], threshold, outpath: Path) -> bool:
    pts = []
    for s in segments:
        try:
            # GenoFLU writes identities like "99.91%" — strip the suffix.
            val = float(str(s.get("percent_identity")).replace("%", "").strip())
            pts.append((s.get("segment") or "?", val))
        except (TypeError, ValueError):
            continue
    if not pts:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = [p[0] for p in pts]
        vals = [p[1] for p in pts]
        fig, ax = plt.subplots(figsize=(6.6, max(1.6, 0.34 * len(labels) + 0.8)))
        ax.barh(labels, vals, color="#4C8C8A")
        ax.set_xlabel("% identity to closest reference (BLASTN)")
        ax.set_title("Per-segment percent identity", color="#1F2A2E", fontsize=11)
        lo = min(90.0, min(vals) - 1) if vals else 90.0
        ax.set_xlim(lo, 100.5)
        try:
            t = float(threshold)
            ax.axvline(t, color="#C46A6A", linestyle="--", linewidth=1)
            ax.text(t, len(labels) - 0.5, f" {t:g}% threshold", color="#C46A6A",
                    fontsize=7, va="top")
        except (TypeError, ValueError):
            pass
        for i, v in enumerate(vals):
            ax.text(v, i, f" {v:g}", va="center", fontsize=8, color="#1F2A2E")
        ax.invert_yaxis()
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(outpath, dpi=150)
        plt.close(fig)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def write_pdf(ctx: Dict[str, Any], path: Path, outdir: Path) -> None:
    ss = _styles()
    sample = ctx["sample"]
    qc = ctx.get("qc") or {}
    result = ctx.get("result") or {}
    man = ctx.get("manifest") or {}
    opts = man.get("options", {}) or {}
    vers = man.get("versions", {}) or {}
    vers_extra = man.get("versions_extra", {}) or {}
    segments = result.get("segments", []) or []
    genotype = result.get("genotype") or "Not assigned"
    complete = bool(result.get("complete"))
    threshold = opts.get("pident_threshold", 98.0)

    assets = outdir / "_report_assets"
    assets.mkdir(exist_ok=True)

    story: List[Any] = []
    story.append(Paragraph("GenoFLU Influenza A Genotyping Report", ss["H1"]))
    story.append(Paragraph(
        f"Sample <b>{sample}</b> &nbsp;·&nbsp; {ctx['date']} &nbsp;·&nbsp; "
        f"GenoFLU {vers.get('genoflu', '?')} &nbsp;·&nbsp; DB {vers.get('reference_db', '?')}",
        ss["Sub"]))

    # Genotype banner
    bfill = SUCCESS if complete else WARN
    story.append(_banner(f"Genotype: {genotype}", bfill, ss))
    qc_verdict = (qc.get("verdict") or "review").lower()
    if qc_verdict != "pass":
        story.append(Spacer(1, 4))
        n = (qc.get("metrics") or {}).get("num_seqs")
        story.append(_banner(
            f"⚠ Input genome QC = review: {int(n) if n else '?'} sequence(s) present "
            "(influenza A has 8 segments). A confident genotype requires all 8 segments.",
            DANGER, ss))
    story.append(Spacer(1, 8))

    # --- Analysis summary (plain language) ---
    story.append(Paragraph("Analysis summary", ss["H2"]))
    matched = result.get("segments_matched") or 0
    if complete:
        summary_txt = (
            f"GenoFLU assigned this influenza A genome to genotype <b>{genotype}</b>. "
            f"All required gene segments matched a known genotype's lineage pattern at "
            f"&ge; {threshold}% nucleotide identity to the curated reference set. "
            f"The per-segment lineage assignments and match statistics are below.")
    else:
        summary_txt = (
            f"GenoFLU did not assign a defined genotype (<b>{genotype}</b>). "
            f"{matched} segment(s) matched a reference lineage at &ge; {threshold}% identity. "
            f"This occurs when fewer than all 8 segments pass the identity threshold, or when "
            f"the segment lineage combination does not match any genotype currently defined in "
            f"the reference key — which can indicate a novel reassortant, an incomplete genome, "
            f"or sequence quality issues. Review the per-segment metrics and input QC below.")
    story.append(Paragraph(summary_txt, ss["Body"]))

    # --- Input file quality ---
    story.append(Paragraph("Input file quality", ss["H2"]))
    m = (qc or {}).get("metrics", {}) or {}
    story.append(_kv_table([
        ("Input FASTA", qc.get("file", "—")),
        ("Segments present", f"{_i(m.get('num_seqs'))} (expected {qc.get('expected_segments', 8)})"),
        ("Total length (bp)", _i(m.get("total_length"))),
        ("Segment length range (bp)", f"{_i(m.get('min_len'))} – {_i(m.get('max_len'))}"),
        ("N50 (bp)", _i(m.get("n50"))),
        ("GC (%)", _f(m.get("gc_pct"))),
        ("QC verdict", (qc.get("verdict") or "—").upper()),
    ], ss))
    seg_rows = qc.get("segments") or []
    if seg_rows:
        story.append(Spacer(1, 4))
        story.append(Paragraph("Per-sequence breakdown of the input FASTA:", ss["Body"]))
        data = [["Sequence", "Length (bp)", "GC%"]]
        for s in seg_rows:
            data.append([str(s.get("name", "")), _i(s.get("length")), _f(s.get("gc_pct"))])
        story.append(_grid(data, ss, [3.8, 1.4, 1.0], small=True))
    for nt in (qc.get("notes") or []):
        story.append(Paragraph(f"• {nt}", ss["Small"]))

    # --- Genotype results ---
    story.append(Paragraph("Genotype &amp; per-segment results", ss["H2"]))
    fig1 = assets / "segment_identity.png"
    if _bar_identity(segments, threshold, fig1):
        story.append(Image(str(fig1), width=6.4 * inch, height=_img_h(fig1, 6.4)))
    if segments:
        story.append(Paragraph(
            "Each row is a gene segment matched to its closest reference lineage. "
            "% identity is the BLASTN identity to that reference; segments below the "
            f"{threshold}% threshold are not counted toward a genotype.", ss["Body"]))
        data = [["Segment", "Lineage", "% identity", "Mismatches", "Avg depth", "Top reference"]]
        for s in segments:
            data.append([
                str(s.get("segment", "")), str(s.get("lineage") or "—"),
                str(s.get("percent_identity") or "—"), str(s.get("mismatches") or "—"),
                str(s.get("avg_depth") or "—"), str(s.get("reference") or "—"),
            ])
        story.append(_grid(data, ss, [0.8, 0.8, 0.9, 0.9, 0.8, 2.0], small=True))
    else:
        story.append(_banner("No segment matched a reference lineage above the identity "
                             "threshold — no per-segment results to display.", TERRA, ss))

    # --- Methods & provenance ---
    story.append(Paragraph("Methods &amp; provenance", ss["H2"]))
    iso = ", ".join(r.get("standard", "") for r in (man.get("iso_references") or []) if r.get("standard"))
    story.append(_kv_table([
        ("GenoFLU", f"{vers.get('genoflu','—')}"),
        ("BLASTN", f"{vers.get('blastn','—')}"),
        ("seqkit", f"{vers_extra.get('seqkit','—')}"),
        ("Reference DB", f"{vers.get('reference_db','—')}"),
        ("Reference DB source", opts.get("genoflu_db") or "—"),
        ("Identity threshold", f"&ge; {threshold}% (BLASTN per segment)"),
        ("Standards referenced", iso or "—"),
    ], ss))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Disclaimer: this is a sequence-based genotype assignment from an assembled genome and a "
        "curated reference key. It characterizes the virus's segment constellation; it is not a "
        "clinical or regulatory determination. A genotype is only as current as the reference key "
        "(see DB version above) — newly emerging reassortants may return 'Not assigned' until the "
        "key is updated. Confirm critical results and follow WOAH/national avian-influenza reporting "
        "requirements. Quality framework: ISO 15189:2022 / ISO/IEC 17025.", ss["Small"]))

    doc = SimpleDocTemplate(
        str(path), pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        title=f"GenoFLU report — {sample}", author="genoflu_gui",
    )
    doc.build(story)


# ---- small helpers ----
def _i(v):
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _f(v):
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—" if v in (None, "") else str(v)


def _img_h(path: Path, width_in: float) -> float:
    """Preserve aspect ratio for an embedded PNG given a target width (inches)."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            w, h = im.size
        return width_in * (h / w) * inch
    except Exception:
        return 2.0 * inch


def _grid(data, ss, col_in, small=False):
    style = ss["Small"] if small else ss["Cell"]
    # Header cells are Paragraph flowables, which ignore the table's TEXTCOLOR,
    # so give the first row its own white, bold style for contrast on the teal.
    hdr_style = ParagraphStyle("GridHdr", parent=style, textColor=colors.white,
                               fontName="Helvetica-Bold")
    body = [[Paragraph(str(c), hdr_style if i == 0 else style) for c in row]
            for i, row in enumerate(data)]
    t = Table(body, colWidths=[c * inch for c in col_in], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F5F2")]),
        ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t
