#!/usr/bin/env python
"""
download_fasta.py — fetch genome FASTAs by accession/BioSample into a project's
download/. GenoFLU genotypes an assembled influenza genome (its 8 segments), so
this is the primary way to seed a project without going through IRMA.

Three input kinds, each with current best practice:

  * Assembly accessions (GCA_/GCF_) -> NCBI **datasets** CLI
        datasets download genome accession <acc> --include genome
    Saved as "<organism>_<strain>_<accession>.fasta" from the assembly report.

  * Nucleotide accessions (NC_/CP_/MN…) -> NCBI eutils
        efetch.fcgi?db=nuccore&id=<acc>&rettype=fasta&retmode=text
    Saved as "<organism>_<accession>.fasta" (one record per file).

  * BioSample (SAMN…/SAMEA…/SAMD…) or a free-text sample name -> eutils
        esearch nuccore for the term, then efetch ALL linked nucleotide records
    **concatenated into ONE multi-FASTA** so GenoFLU sees a single genome with
    its 8 segments. This is the influenza case: one BioSample -> one genome.

Names are informative so downstream tables carry metadata; a
fasta_download_crosswalk.tsv records accession -> organism/strain -> file. Each
input soft-fails independently so one bad ID doesn't sink the batch.

Usage:
  download_fasta.py --outdir DIR --accessions SAMN60641678 GCA_000195835.3 ...
      [--no-rename] [--email you@example.org]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_NCBI_API_KEY = (os.environ.get("NCBI_API_KEY") or "").strip()
_MIN_INTERVAL = 0.11 if _NCBI_API_KEY else 0.40
_RETRY_BACKOFFS = (1.0, 2.0, 4.0, 8.0)
_EMAIL = (os.environ.get("NCBI_EMAIL") or "genoflu_gui@kapurlab.local").strip()
_last_call_at = 0.0

_ASSEMBLY_RE = re.compile(r"^GC[AF]_\d+(?:\.\d+)?$", re.IGNORECASE)
_BIOSAMPLE_RE = re.compile(r"^SAM[NED]\w+$", re.IGNORECASE)
# A bare nucleotide accession: 1-2 letters, optional '_', 4+ digits, optional .version.
_NUC_ACC_RE = re.compile(r"^[A-Za-z]{1,2}_?\d{4,}(?:\.\d+)?$")
_FNA_GLOBS = ("*_genomic.fna", "*.fna", "*.fasta", "*.fa")
# Max nucleotide records pulled for one BioSample / query (flu = 8 segments).
_MAX_LINKED = 50


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _sanitize(stem: str, maxlen: int = 80) -> str:
    """Filesystem-safe name stem: keep [A-Za-z0-9_-], replace the rest with '_'.
    Dots are removed so an accession version like 'GCA_000195835.3' becomes
    'GCA_000195835_3' (the '.fasta' extension is added by the caller)."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", stem)
    name = re.sub(r"_{2,}", "_", name).strip("_-")
    return (name or "genome")[:maxlen].strip("_-") or "genome"


def _is_assembly(acc: str) -> bool:
    return bool(_ASSEMBLY_RE.match(acc.strip()))


def _is_biosample(acc: str) -> bool:
    return bool(_BIOSAMPLE_RE.match(acc.strip()))


def _is_plain_nuc(acc: str) -> bool:
    return bool(_NUC_ACC_RE.match(acc.strip()))


# ---------------------------------------------------------------------------
# eutils GET (rate-limited, 429-backoff)
# ---------------------------------------------------------------------------
def _eutils_get(url: str, timeout: int = 60) -> bytes:
    global _last_call_at
    if _NCBI_API_KEY and "api_key=" not in url:
        url += ("&" if "?" in url else "?") + "api_key=" + _NCBI_API_KEY
    for attempt in range(len(_RETRY_BACKOFFS) + 1):
        elapsed = time.monotonic() - _last_call_at
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                _last_call_at = time.monotonic()
                return resp.read()
        except urllib.error.HTTPError as e:
            _last_call_at = time.monotonic()
            if e.code in (429, 500, 502, 503) and attempt < len(_RETRY_BACKOFFS):
                time.sleep(_RETRY_BACKOFFS[attempt])
                continue
            raise
    raise RuntimeError("unreachable")


def _organism_from_defline(defline: str) -> str:
    """'>CY012345 Influenza A virus (A/.../2023(H5N1)) ...' -> the organism words."""
    text = defline.lstrip(">").strip()
    parts = text.split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    rest = re.split(r",\s|\s\(", rest)[0]
    return " ".join(rest.split()[:8]).strip()


def _esearch_nuccore(term: str, retmax: int = _MAX_LINKED) -> List[str]:
    """Return a list of nuccore accession-or-UID hits for a search term."""
    params = urllib.parse.urlencode({
        "db": "nuccore", "term": term, "retmax": retmax, "retmode": "json",
        "tool": "genoflu_gui", "email": _EMAIL,
    })
    data = _eutils_get(f"{_EUTILS}/esearch.fcgi?{params}")
    try:
        res = json.loads(data.decode("utf-8", "replace"))
        return list(res.get("esearchresult", {}).get("idlist", []) or [])
    except (ValueError, KeyError):
        return []


# ---------------------------------------------------------------------------
# Nucleotide accession -> efetch FASTA (single record)
# ---------------------------------------------------------------------------
def fetch_nucleotide(acc: str, outdir: Path, rename: bool) -> Dict[str, str]:
    rec = {"accession": acc, "type": "nucleotide", "organism": "", "strain": "",
           "output_file": "", "status": "ok"}
    params = urllib.parse.urlencode({
        "db": "nuccore", "id": acc, "rettype": "fasta", "retmode": "text",
        "tool": "genoflu_gui", "email": _EMAIL,
    })
    data = _eutils_get(f"{_EUTILS}/efetch.fcgi?{params}")
    text = data.decode("utf-8", "replace")
    if not text.lstrip().startswith(">"):
        raise ValueError(f"efetch did not return FASTA for {acc}: {text[:120]!r}")
    first = text.splitlines()[0]
    organism = _organism_from_defline(first)
    rec["organism"] = organism
    base = _sanitize(f"{organism}_{acc}") if (rename and organism) else _sanitize(acc)
    out = _unique(outdir / f"{base}.fasta")
    out.write_text(text, encoding="utf-8")
    rec["output_file"] = out.name
    log(f"  [nuccore] {acc} -> {out.name}  ({organism or 'no organism in defline'})")
    return rec


# ---------------------------------------------------------------------------
# BioSample / sample name -> combined multi-FASTA (all linked segments)
# ---------------------------------------------------------------------------
def fetch_biosample(token: str, outdir: Path, rename: bool) -> Dict[str, str]:
    """Resolve a BioSample accession (or free-text sample name) to its linked
    nucleotide records and write them as ONE multi-FASTA genome, so GenoFLU
    reads all segments together."""
    rec = {"accession": token, "type": "biosample", "organism": "", "strain": "",
           "output_file": "", "status": "ok"}
    term = f"{token}[BioSample]" if _is_biosample(token) else token
    ids = _esearch_nuccore(term)
    if not ids and not _is_biosample(token):
        # Fall back to an all-fields search for a bare sample name.
        ids = _esearch_nuccore(f"{token}[All Fields]")
    if not ids:
        raise ValueError(f"no nucleotide records found for {token!r} "
                         f"(searched nuccore for {term!r})")
    params = urllib.parse.urlencode({
        "db": "nuccore", "id": ",".join(ids), "rettype": "fasta", "retmode": "text",
        "tool": "genoflu_gui", "email": _EMAIL,
    })
    data = _eutils_get(f"{_EUTILS}/efetch.fcgi?{params}")
    text = data.decode("utf-8", "replace")
    if not text.lstrip().startswith(">"):
        raise ValueError(f"efetch returned no FASTA for {token}: {text[:120]!r}")
    n_seqs = text.count(">")
    organism = _organism_from_defline(text.splitlines()[0])
    rec["organism"] = organism
    base = _sanitize(f"{organism}_{token}") if (rename and organism) else _sanitize(token)
    out = _unique(outdir / f"{base}.fasta")
    out.write_text(text, encoding="utf-8")
    rec["output_file"] = out.name
    log(f"  [biosample] {token} -> {out.name}  ({n_seqs} segment/record(s); {organism or 'organism unknown'})")
    return rec


# ---------------------------------------------------------------------------
# Assembly accession -> datasets CLI
# ---------------------------------------------------------------------------
def _read_assembly_report(extract_dir: Path) -> Dict[str, str]:
    info = {"organism": "", "strain": "", "accession": ""}
    reports = list(extract_dir.rglob("assembly_data_report.jsonl"))
    if not reports:
        return info
    try:
        line = reports[0].read_text(encoding="utf-8", errors="replace").splitlines()[0]
        rec = json.loads(line)
    except (OSError, ValueError, IndexError):
        return info
    info["accession"] = rec.get("accession", "")
    org = rec.get("organism", {}) or {}
    info["organism"] = org.get("organismName", "") or ""
    infra = org.get("infraspecificNames", {}) or {}
    info["strain"] = infra.get("strain") or infra.get("isolate") or ""
    return info


def fetch_assembly(acc: str, outdir: Path, rename: bool) -> Dict[str, str]:
    rec = {"accession": acc, "type": "assembly", "organism": "", "strain": "",
           "output_file": "", "status": "ok"}
    if shutil.which("datasets") is None:
        raise RuntimeError("NCBI 'datasets' CLI not on PATH — cannot fetch assembly "
                           f"{acc}. Install ncbi-datasets-cli (deploy/install.sh).")
    work = outdir / ".fasta_dl_tmp" / _sanitize(acc)
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    zip_path = work / f"{_sanitize(acc)}.zip"
    cmd = ["datasets", "download", "genome", "accession", acc,
           "--include", "genome", "--no-progressbar", "--filename", str(zip_path)]
    log(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not zip_path.is_file():
        raise RuntimeError(f"datasets download failed for {acc}: "
                           f"{(proc.stderr or proc.stdout or '').strip()[:200]}")
    extract = work / "extracted"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract)

    meta = _read_assembly_report(extract)
    rec["organism"] = meta.get("organism", "")
    rec["strain"] = meta.get("strain", "")

    fna = None
    data_dir = extract / "ncbi_dataset" / "data"
    for pat in _FNA_GLOBS:
        hits = sorted((data_dir).rglob(pat)) if data_dir.is_dir() else sorted(extract.rglob(pat))
        if hits:
            fna = hits[0]
            break
    if fna is None:
        raise RuntimeError(f"no genome FASTA found in datasets package for {acc}")

    if rename and rec["organism"]:
        stem = rec["organism"]
        if rec["strain"] and _sanitize(rec["strain"]) not in _sanitize(stem):
            stem = f"{stem}_{rec['strain']}"
        base = _sanitize(f"{stem}_{acc}")
    else:
        base = _sanitize(acc)
    out = _unique(outdir / f"{base}.fasta")
    shutil.copyfile(fna, out)
    rec["output_file"] = out.name
    shutil.rmtree(work, ignore_errors=True)
    log(f"  [assembly] {acc} -> {out.name}  ({rec['organism']} {rec['strain']})".rstrip())
    return rec


def _unique(path: Path) -> Path:
    """Avoid clobbering an existing file: append _2, _3, … before the suffix."""
    if not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


def _write_crosswalk(outdir: Path, records: List[Dict[str, str]]) -> None:
    cw = outdir / "fasta_download_crosswalk.tsv"
    header = "accession\ttype\torganism\tstrain\toutput_file\tstatus\n"
    rows = [header]
    for r in records:
        rows.append("\t".join([r.get("accession", ""), r.get("type", ""),
                               r.get("organism", ""), r.get("strain", ""),
                               r.get("output_file", ""), r.get("status", "")]) + "\n")
    mode = "a" if cw.is_file() else "w"
    with cw.open(mode, encoding="utf-8") as fh:
        if mode == "w":
            fh.write(rows[0])
        fh.writelines(rows[1:])


def fetch_one(acc: str, outdir: Path, rename: bool) -> Dict[str, str]:
    """Dispatch one input token to the right fetcher."""
    if _is_assembly(acc):
        return fetch_assembly(acc, outdir, rename)
    if _is_biosample(acc) or not _is_plain_nuc(acc):
        # BioSample or a free-text sample name -> combined multi-FASTA genome.
        return fetch_biosample(acc, outdir, rename)
    return fetch_nucleotide(acc, outdir, rename)


def _kind(acc: str) -> str:
    if _is_assembly(acc):
        return "assembly"
    if _is_biosample(acc) or not _is_plain_nuc(acc):
        return "biosample"
    return "nucleotide"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Download genome FASTAs by accession/BioSample.")
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--accessions", nargs="+", required=True)
    ap.add_argument("--no-rename", action="store_true",
                    help="Save files as the bare accession instead of metadata-derived names.")
    ap.add_argument("--email", default=None)
    args = ap.parse_args(argv)
    if args.email:
        global _EMAIL
        _EMAIL = args.email.strip()

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    rename = not args.no_rename

    seen, accs = set(), []
    for a in args.accessions:
        a = a.strip()
        if a and a not in seen:
            seen.add(a)
            accs.append(a)

    log("=" * 64)
    log(f"FASTA download — {len(accs)} input(s) -> {outdir}")
    log(f"  metadata renaming: {'on' if rename else 'off'}")
    log("=" * 64)

    records: List[Dict[str, str]] = []
    ok = 0
    for acc in accs:
        try:
            records.append(fetch_one(acc, outdir, rename))
            ok += 1
        except Exception as exc:  # noqa: BLE001 — soft-fail per input
            log(f"  ERROR: {acc}: {exc}")
            records.append({"accession": acc, "type": _kind(acc), "organism": "",
                            "strain": "", "output_file": "", "status": f"failed: {exc}"})

    _write_crosswalk(outdir, records)
    shutil.rmtree(outdir / ".fasta_dl_tmp", ignore_errors=True)

    log("")
    log(f"Done: {ok}/{len(accs)} downloaded. Crosswalk: fasta_download_crosswalk.tsv")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
