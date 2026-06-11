# GenoFLU GUI

A web interface for **USDA-VS [GenoFLU](https://github.com/USDA-VS/GenoFLU)** —
whole-genome **genotyping of North-American H5N1 2.3.4.4b** influenza A viruses.
GenoFLU BLASTs each of the 8 gene segments against a curated reference set and
matches the per-segment lineage pattern to a genotype key.

Part of the Kapur Lab Open OnDemand pipeline family (`vsnp_gui`,
`kraken_id_parse_gui`, `amr_plus_gui`, `mlst_gui`) — same look, deploy model, and
shared project layout. FastAPI backend + React (Vite) SPA, one uvicorn per OOD
session.

## Input & output

- **Input:** an assembled influenza-A FASTA (ideally all 8 segments in one file),
  one FASTA per sample. Projects are shared with the sibling tools at
  `/srv/kapurlab/projects/`.
- **Per sample, in `<project>/genoflu/<sample>/`:**
  - `report.pdf` — input QC, plain-language summary, genotype + per-segment table
    (with a %identity figure), and a methods/provenance page with ISO references
    and an interpretation disclaimer.
  - `<sample>_<date>_stats.xlsx` — statistics in a **single labeled column**
    (`Statistic | Value`), modelled on the vSNP3 stats workbook.
  - `genoflu_genotype.tsv` / `.xlsx` — GenoFLU's native genotype output.
  - `genoflu_result.json` — parsed genotype + per-segment metrics.
  - `fasta_qc.json` — seqkit input-FASTA QC.
  - `run_manifest.json` — full provenance: every option, tool + reference-DB
    version, the identity threshold, and the standards referenced.

## Quality & standards

Every run records the BLASTN percent-identity threshold (default **98%**,
the USDA-VS surveillance value), per-segment % identity / mismatches / average
depth, and the reference-DB version, so a genotype call can be verified and
defended. Provenance references **ISO 15189:2022** and **ISO/IEC 17025** (lab
quality/competence), the **WOAH Terrestrial Manual Ch. 3.3.4** avian-influenza
standard, and **WHO/WOAH/FAO H5 clade nomenclature**.

## Install & deploy

See [`deploy/INSTALL.md`](deploy/INSTALL.md). Quickstart:

```bash
deploy/install.sh --conda-base /srv/kapurlab/tools/miniforge3   # env + frontend
sudo deploy/register_ood_apps.sh                                # dashboard apps (root)
```

The reference database is bundled with the `genoflu` conda package. Update
genotypes with `deploy/update_genoflu.sh`.

## Dashboard

Two OOD apps register under **Interactive Apps → Bioinformatics → Influenza
Genotyping**:
- **GenoFLU** — production (serves the committed `frontend/dist/`).
- **GenoFLU (dev)** — pick a Git branch; per-session worktree, frontend rebuilt
  on launch. Not for production data.

## Development

Edit, then: `frontend/src` → `npm run build`; `backend/app` → new session;
`bin/` → next run; `ood/**` → re-run `sudo deploy/register_ood_apps.sh`.
See [`CLAUDE.md`](CLAUDE.md) for the full conventions and constraints.
