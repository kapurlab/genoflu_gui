# GenoFLU GUI — Claude Code Context

> Read this before editing. This is one of the Kapur Lab OOD pipeline tools
> (siblings: `vsnp_gui`, `kraken_id_parse_gui`, `amr_plus_gui`, `mlst_gui`).
> The shared conventions + gotchas live in
> `/srv/kapurlab/tools/amr_plus_gui/docs/BUILDING_A_SIBLING_TOOL.md` — read that too.

## What this is

A web GUI for **USDA-VS GenoFLU** — whole-genome **genotyping of North-American
H5N1 2.3.4.4b** influenza A viruses. GenoFLU BLASTs each of the 8 gene segments
against a curated reference set and matches the per-segment lineage pattern to a
genotype key. Input is an **assembled FASTA** (all 8 segments). Output is a
genotype call plus per-segment %identity / mismatch / depth.

FastAPI backend + React (Vite) SPA, deployed as an **Open OnDemand
batch_connect** app. One uvicorn per user session behind OOD's Apache proxy at
`/rnode/<host>/<port>/`. FastAPI serves `frontend/dist/`. Genotyping runs as a
background subprocess tracked by `JobManager`, logs streamed over SSE.

## Layout

```
/srv/kapurlab/tools/genoflu_gui/
  backend/app/
    main.py        all FastAPI routes (projects, run, jobs, geno-results/geno-table)
    config.py      load/save per-user ~/.config/genoflu_gui/config.json
    jobs.py        JobManager (PID marker "genoflu"); reused from the siblings
    sra.py         SRA accession helpers (reused)
  bin/
    genoflu_pipeline.py  orchestrator: FASTA QC (seqkit) -> GenoFLU -> report
    run_genoflu.py       GenoFLU runner + run_manifest.json provenance (ISO refs)
    reporting/           stats_excel.py (single-column xlsx) + pdf_report.py
  conda_setup/environment.yml   env name genoflu_gui (genoflu, blast, seqkit, web+report deps)
  deploy/install.sh             idempotent env+frontend install (portable, no sudo)
  deploy/update_genoflu.sh      bump the GenoFLU conda package (tool + reference DB)
  deploy/register_ood_apps.sh   copy ood/apps/* into /var/www/ood/apps/sys (root)
  ood/apps/genoflu_gui{,_dev}/  OOD app definitions (prod + dev branch-picker)
  frontend/src/App.jsx          the SPA; App.css is the shared theme (verbatim)
```

## Hard constraints (break -> silent failure)

1. **All frontend URLs relative** (`fetch("./api/...")`, `new EventSource("./api/jobs/<id>/log")`).
   `vite.config.js` keeps `base: "./"`. The browser origin is the OOD server, not the app.
2. **FastAPI serves the SPA** from `frontend/dist/`. No separate static server.
3. **Rebuild the frontend after any `frontend/src` edit** (`npm run build`); uvicorn serves `dist/`.
4. **Use the env Python** `/srv/kapurlab/tools/genoflu_gui/env/bin/python`, never system/base.
5. **`<env>/bin` must be on PATH** when GenoFLU runs — it shells out to `blastn`/`makeblastdb`.
   The OOD `script.sh.erb` and `install.sh` set this. (No `$CONDA_PREFIX` needed — unlike amrfinder.)

## How a run works

`POST /api/run {project, fasta, pident?, genoflu_db?}` ->
`bin/genoflu_pipeline.py --sample S --outdir <project>/genoflu/S --fasta F --pident 98 [--genoflu-db D]`:
1. **seqkit** on the FASTA -> `fasta_qc.json` (segment count/lengths/GC/N50; verdict pass if 8 segments).
2. **GenoFLU** (`run_genoflu.py`) -> `genoflu_genotype.{tsv,xlsx}` (native), `genoflu_result.json`
   (parsed genotype + per-segment), `run_manifest.json` (every option, tool+DB versions, ISO refs).
3. **reporting** -> `<sample>_<date>_stats.xlsx` (single labeled column, vSNP3-style) + `report.pdf`.

Per-sample results are read straight off disk from `<project>/genoflu/<sample>/`, so any past run
is revisitable. Results categories/labels + media handling live in `main.py`.

## Reference database & updates

GenoFLU's reference set (`dependencies/fastas` + `dependencies/genotype_key.xlsx`) ships **inside
the conda package** at `<env>/dependencies/`. `run_genoflu.py` resolves it relative to the installed
`genoflu.py`; `config.py` `genoflu_db` overrides it only to point at an out-of-tree set. The version
in use (genotype count + key date) is recorded in `run_manifest.json`.

**To update genotypes** as USDA-VS releases them: `deploy/update_genoflu.sh` bumps the pinned
`genoflu` conda package and re-verifies. Then pin the new version in `conda_setup/environment.yml`.
The next pipeline run uses the update immediately (no rebuild).

## Reloads (what picks up an edit)

- `bin/` scripts -> next pipeline run (subprocess reads from disk).
- `backend/app/` -> new OOD session (or `--reload` in the dev app).
- `frontend/src` -> `npm run build`, then a new session.
- `ood/**` -> re-run `sudo deploy/register_ood_apps.sh` (the registered copy is a snapshot).

## Key paths

| Item | Path |
|---|---|
| Env Python | `/srv/kapurlab/tools/genoflu_gui/env/bin/python` |
| genoflu CLI | `<env>/bin/genoflu.py` |
| Reference DB | `<env>/dependencies/{fastas,genotype_key.xlsx}` |
| Shared projects | `/srv/kapurlab/projects/` |
| OOD app (deployed) | `/var/www/ood/apps/sys/genoflu_gui{,_dev}` |
| OOD app (source) | `/srv/kapurlab/tools/genoflu_gui/ood/apps/` <- edit here |
