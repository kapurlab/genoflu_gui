# Deploying the GenoFLU GUI on an Open OnDemand system

This tool follows the Kapur Lab pipeline family pattern (vsnp_gui,
kraken_id_parse_gui, amr_plus_gui, mlst_gui). It is designed to be portable to
other OnDemand hosts: no hard-coded user `$HOME` paths, a shared conda env at
`<repo>/env`, and a reference DB that ships inside the conda package.

## 1. Install (no sudo)

```bash
cd /srv/kapurlab/tools/genoflu_gui
deploy/install.sh --conda-base /srv/kapurlab/tools/miniforge3   # shared env at <repo>/env
# preview first with:  deploy/install.sh --dry-run
# personal env instead: deploy/install.sh --personal           # env name genoflu_gui
```

`install.sh` is idempotent:
1. Creates the conda env (`mamba env create -p <repo>/env -f conda_setup/environment.yml`).
2. `pip install backend/requirements.txt` into it.
3. Verifies `genoflu`, `blastn`, `seqkit` and the bundled reference DB
   (`<env>/dependencies/fastas` + `genotype_key.xlsx`).
4. Builds the React frontend (`frontend/dist/`).

Flags: `--skip-verify`, `--skip-frontend`, `--dry-run`, `--personal`, `--conda-base DIR`.

## 2. Reference database

GenoFLU's reference set is **bundled in the conda package** — nothing to download.
`config.py`'s `genoflu_db` is blank by default, so the runner resolves the DB
relative to the installed `genoflu.py`. The version used is recorded in every
run's `run_manifest.json`.

**Update genotypes** when USDA-VS releases new ones:

```bash
deploy/update_genoflu.sh                 # newest genoflu (tool + reference DB)
deploy/update_genoflu.sh --version 1.07  # pin a specific release
```

Then pin the new version in `conda_setup/environment.yml`.

## 3. Register the OOD apps (root)

```bash
sudo deploy/register_ood_apps.sh
```

This copies `ood/apps/genoflu_gui` and `ood/apps/genoflu_gui_dev` into
`/var/www/ood/apps/sys/`. They then appear under **Interactive Apps →
Bioinformatics → Influenza Genotyping**. The prod app serves the committed
on-disk `dist/`; the `_dev` app checks out a chosen Git branch into a per-session
`/tmp` worktree and rebuilds the frontend per launch (`--reload`).

> The curated "Kapur Lab Pipelines" landing page is separate from the registered
> apps — add the GenoFLU card there by hand.

## 4. Config keys (`~/.config/genoflu_gui/config.json`)

| Key | Default | Notes |
|---|---|---|
| `projects_root` | `~/projects` | personal projects; shared at `/srv/kapurlab/projects` always visible |
| `genoflu_db` | blank | optional override; blank = bundled package DB |
| `pident_threshold` | `98.0` | BLASTN per-segment match threshold (USDA-VS value) |

## 5. Smoke test on the CLI

```bash
ENVPY=/srv/kapurlab/tools/genoflu_gui/env/bin/python
export PATH=/srv/kapurlab/tools/genoflu_gui/env/bin:$PATH
PYTHONPATH=/srv/kapurlab/tools/genoflu_gui/bin \
  $ENVPY /srv/kapurlab/tools/genoflu_gui/bin/genoflu_pipeline.py \
  --sample TEST --outdir /tmp/genoflu_test --fasta <your_genome.fasta>
ls /tmp/genoflu_test   # report.pdf, *_stats.xlsx, genoflu_genotype.tsv, run_manifest.json, ...
```
