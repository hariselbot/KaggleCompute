# KaggleCompute Connector - Integration Contract

This is the stable interface your code integrates against. Everything else in
the repo (profiles, runner, ledger) is implementation you can rely on but never
need to touch. If you own an external pipeline (e.g. photogrammetry), you only
need this document.

## Mental model

```
your system                    KaggleCompute                     Kaggle
------------                 --------------                    -------
upload images   --dataset-->  input dataset (yours, private)
submit job      --YAML----->  connector.py submit  ---------->  kernel starts (12 h max)
                              runner executes stages,           2x T4 / 4 CPU / 30 GB RAM
                              checkpoints after each
poll            <------------ connector.py status  <----------  kernel state + job manifest
download        <------------ connector.py download <---------  output dataset version
```

Transport is **Kaggle datasets**: inputs go in as a dataset (version), outputs
come out as a new dataset version. 200 GB private quota. If a job needs to push
outputs somewhere else (Drive, S3, your server), it can - the job controls its
own code, so any reachable destination works; dataset output is simply the
built-in default.

## 1. The job file (what you POST, conceptually)

```yaml
job_id: facade-scan-014          # required, [a-z0-9-], unique per run
profile: photogrammetry          # required: photogrammetry | llm-serve | workbench | <future>
params:                          # merged over the profile's declared defaults
  input_dataset: youruser/facade-scan-014-images
  matcher: exhaustive
  dense: true
  mesher: poisson
  gpu_minutes_estimate: 180      # used for budget admission control
notify: {}                       # reserved
```

Parameter resolution: profile defaults < `config/farm.config.yaml` < job
`params`. No value is ever silently hard-coded; if a parameter is missing and
has no default, the job fails fast with a named error.

## 2. Submit

```bash
python runner/connector.py submit --job path/to/job.yaml
```

- Validates the job against the profile's parameter schema.
- Checks the budget ledger first: if the job's `gpu_minutes_estimate` would
  exceed the remaining weekly budget, submission is **refused** with exit
  code 3 and `status: queued-budget` - nothing is consumed.
- Packages the runner + profile code into the kernel itself (self-contained;
  the kernel never needs repo access), pushes it via the Kaggle API, and
  starts the run.
- Returns immediately. Runs are async; sessions cap at 12 h.

## 3. Status

```bash
python runner/connector.py status --job-id facade-scan-014
```

Two layers, merged in the response:

- **Kernel state** (from the Kaggle API): queued / running / complete / error.
- **Job manifest** (from the job itself, once stages begin): per-stage status,
  GPU-minutes consumed so far, output locations. Schema:

```json
{
  "job_id": "facade-scan-014",
  "profile": "photogrammetry",
  "status": "running | complete | failed | queued-budget",
  "stages": [{"name": "extract", "gpu": true, "status": "done", "minutes": 21.4}],
  "gpu_minutes_total": 38.2,
  "outputs": {"dataset": "youruser/kc-out-facade-scan-014", "files": ["mesh.ply", "sparse/", "dense/"]},
  "error": null,
  "updated_at": "2026-09-27T18:02:11Z"
}
```

## 4. Download

```bash
python runner/connector.py download --job-id facade-scan-014 --dest ./out
```

Pulls the output dataset version (default) or raw kernel output files.

## 5. Budget semantics (free tier only - no paid overflow)

- Weekly budget: **1800 GPU-minutes** (30 h), rolling 7-day window, configured
  in `config/farm.config.yaml`.
- Kaggle does not expose remaining quota via API, so the ledger self-accounts:
  every GPU stage's wall time is recorded (multiplied by
  `quota.drain_multiplier`, default 1, to be calibrated against the live quota
  page on first sessions).
- When the budget is exhausted, new jobs are refused (`queued-budget`) until
  the rolling window frees capacity. Nothing ever spends money because there
  is nothing to spend: the free tier is a hard wall, and the ledger stops us
  at it cleanly instead of mid-stage.
- Per-path caps (`paths.*.weekly_gpu_minutes`) are optional subdivisions of
  the global budget. `null` = uncapped, first-come-first-served.

## 6. Failure semantics

| Situation | What you see | What to do |
|---|---|---|
| Session hits the 12 h cap mid-job | kernel `error`/`cancelled`, manifest shows completed stages | Resubmit the same job file. Done stages are skipped via checkpoints; the job resumes. |
| Budget exhausted | submit exit 3, `queued-budget` | Wait for the rolling window, or raise the path cap. |
| Stage failure | manifest `failed` + `error` message, per-stage log tail | Fix inputs/params, resubmit; completed upstream stages are reused. |
| Bad job file | submit exit 2, validation error naming the field | Fix and resubmit. |

Exit codes: `0` ok, `2` validation error, `3` budget refusal, `4` Kaggle API
error, `5` internal.

## 7. Writing your own profile (optional)

A profile is a directory:

```
profiles/your-task/
  profile.yaml   # name, path, params with defaults, stages with gpu flags
  run.sh         # called once per stage: run.sh <stage> <work_dir>; params in $KC_PARAMS (JSON)
```

Stages run in order; a stage marked done (checkpoint) is never re-run on
resume. GPU stages are timed and charged to the ledger. If your pipeline is
already a working script, the `workbench` profile wraps it as-is - profiles
are for tasks you want parameterized and repeatable.

## 8. One-time prerequisites (account owner)

See `docs/kaggle-setup.md`: phone verification (required for free GPU +
internet), one manual T4x2 enable, API token for the CLI. Your integration
never needs the owner's credentials beyond the Kaggle API token it is given.

## 8. LLM proxy (model calls, no GPU involved)

Separate from kernel jobs: `proxy/` routes plain LLM chat calls from your local
machine through Kaggle's hosted Model Proxy (the Kaggle Benchmarks AI credit,
dollar-denominated). Nothing runs in a Kaggle session; no GPU quota is touched.

```bash
python proxy/cli.py auth                 # mint/refresh proxy credentials now
python proxy/cli.py quota                # show Kaggle AI-credit quota + admission state
python proxy/cli.py models               # list catalog models; flags if default_model is missing
python proxy/cli.py chat --prompt "Summarize this log" [--model claude-sonnet-5]
```

Python API:

```python
from proxy import client
resp = client.chat([{"role": "user", "content": "hello"}], model="gpt-6-astra")
print(resp["choices"][0]["message"]["content"])
```

**Kaggle key priority (input wins over files):**
`--kaggle-key` > `KC_KAGGLE_KEY` env > config `llm_proxy.kaggle_key` >
`KAGGLE_API_TOKEN` env > `~/.kaggle/access_token` > `~/.kaggle/kaggle.json`
(classic JSON or raw token). Keys are never written to the repo; minted proxy
credentials live in `.kc-llm-state/` (gitignored) and auto re-mint
`refresh_margin_seconds` before their server-set expiry.

**Budget semantics.** `kaggle b quota` (recent CLI) reports Daily/Monthly used,
remaining, total, refillAt. `llm_proxy.daily_usd_cap` / `monthly_usd_cap` are OUR
caps - set them at or below Kaggle's refill amounts. Chat calls are refused with
exit 3 once usage hits a cap, and free up at refill. If the quota command is
unavailable (CLI 1.7.x from PyPI lacks it; install the CLI from
github.com/Kaggle/kaggle-cli, needs Python 3.11+), admission falls back to
EXACT local accounting: responses carry per-call dollar costs, summed per
UTC day/month from `.kc-llm-state/llm-usage.jsonl`. Kaggle's server-side
budget is a hard wall at $0 regardless; there is no paid overflow.

**Verified behavior (2026-09-28, jarri81).** The locally minted token serves a
CURATED SUBSET of the catalog, not the full list: 8 models at mint time
(Claude Sonnet 5, DeepSeek-R1, Gemini 3 Flash / 3.1 Flash-Lite, IBM Granite
4.0 H Small, GPT-5.4 nano, gpt-oss-120b, Qwen3 Next 80B). See the exact set in
`LLMS_AVAILABLE` from `kaggle b init -y`; use those vendor/model slug strings
(e.g. `anthropic/claude-sonnet-5@default`) - catalog slugs from
`kaggle b t models` are not all callable locally. Proxy tokens live ~2 hours
(server-set expiry); `proxy/auth.py` re-mints automatically. Responses include
exact per-call dollar costs (`usage.cost.*_nanodollars`), so the usage log is
real dollar accounting. The proxy speaks OpenAI chat completions at
`<base>/openapi` (plus a Google GenAI flavor at `<base>/genai`); temperature
is currently unsupported upstream. This is Benchmarks local-development
surface used as a general router - keep volume modest and don't make it your
only model path.
