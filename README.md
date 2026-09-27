# KaggleCompute

Free-tier GPU compute on Kaggle, run as a small farm: one repo, one runner, one
quota ledger, and **profiles** for every kind of work. Built connector-first so
external pipelines (e.g. a photogrammetry team's codebase) integrate against a
stable submit/status/download interface instead of reimplementing Kaggle glue.

Budget reality (Kaggle free tier, verified 2026-09-27):

- **30 GPU-hours/week** (rolling weekly reset)
- **12 h max** per session (CPU and GPU)
- Per session: 2x Tesla T4 (16 GB VRAM each), 4 CPU cores, ~30 GB RAM
- CPU-only sessions are unlimited and do not touch the GPU quota
- 200 GB of private Kaggle Datasets for inputs/outputs/checkpoints/state

## Concepts

| Concept | What it is |
|---|---|
| **Profile** | A directory declaring one kind of task: parameters, install steps, ordered stages with a `gpu: true/false` flag per stage. Adding a task type = adding a directory, nothing else changes. |
| **Job** | A small YAML file: which profile, parameter overrides, input dataset. The unit of work. |
| **Path** | A logical lane with its own budget accounting: `batch` (long jobs), `interactive` (live serving), `workbench` (arbitrary experiments). |
| **Runner** | `runner/run.py` - executes a job's stages inside a Kaggle session, checkpoints after each stage, resumes after session death, enforces the budget, writes a manifest. |
| **Ledger** | Self-accounting GPU-minute record. Kaggle's API does not expose remaining quota, so the ledger computes rolling 7-day usage from its own records and refuses work that would overrun. |
| **Connector** | `runner/connector.py` + `CONNECTOR.md` - the submit/status/download interface external systems integrate against. |

## Profiles shipped in v1

| Profile | Path | What it does |
|---|---|---|
| `photogrammetry` | batch | COLMAP sparse+dense reconstruction + Open3D meshing. Reference implementation; your own pipeline can replace it while keeping the same job contract. |
| `llm-serve` | interactive | Ollama serving an OpenAI-compatible endpoint through a key-protected tunnel. Chatbot vs coding tool is the same profile with a different `model` parameter. |
| `workbench` | workbench | Escape hatch: sync any folder of code+data into a GPU session and run any command. |

## Quickstart (headless)

```bash
# one-time, on any machine with the Kaggle CLI authenticated (docs/kaggle-setup.md)
pip install kaggle pyyaml

# submit a job
python runner/connector.py submit --job jobs/examples/photogrammetry-example.job.yaml

# watch it
python runner/connector.py status --job-id my-first-job

# fetch outputs
python runner/connector.py download --job-id my-first-job --dest ./out
```

## Quickstart (web)

Open the matching thin notebook in `notebooks/`, edit the `JOB` dict in the
first cell, enable the T4x2 accelerator (Settings -> Accelerator), Run All.

## Fetching run artifacts

`kaggle kernels output` is basic-auth and Kaggle denies it for our kernels
("Permission 'kernels.get' was denied"). Use the Bearer helper instead:

```
python3 tools/kc_download.py <username>/<kernel-slug> --list
python3 tools/kc_download.py <username>/<kernel-slug> --out ./artifacts --include output/
```

Auth: `$KAGGLE_API_TOKEN`, else the key from `~/.kaggle/kaggle.json`.

## Docs

- `CONNECTOR.md` - integration contract for external dev teams (**start here if you are integrating**)
- `docs/kaggle-setup.md` - one-time account setup (phone verification, T4x2, API token)
- `config/farm.config.yaml` - global budget and path configuration
