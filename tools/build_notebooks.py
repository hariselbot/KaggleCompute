#!/usr/bin/env python3
"""Generate the thin web notebooks with the runner+profile bundle embedded.

Each notebook: (1) an editable JOB dict cell, (2) a self-contained bootstrap
cell that unpacks the code bundle and runs the job. Regenerate after changing
runner/ or profiles/ code: python3 tools/build_notebooks.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
import connector  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

BOOTSTRAP = '''\
# --- KaggleCompute bootstrap (generated - do not edit) ---
import base64, io, subprocess, sys, zipfile, yaml
from pathlib import Path

BUNDLE = """{bundle}"""

work = Path("/kaggle/working")
root = work / "kc-pkg"
root.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(base64.b64decode(BUNDLE))) as z:
    z.extractall(root)
(root / "job.yaml").write_text(yaml.safe_dump(JOB))
rc = subprocess.call([
    sys.executable, str(root / "runner" / "run.py"),
    "--profile", str(root / "profiles" / JOB["profile"]),
    "--job", str(root / "job.yaml"),
])
print("exit code:", rc)
'''

JOB_CELLS = {
    "photogrammetry": {
        "job_id": "my-scan-001",
        "profile": "photogrammetry",
        "params": {
            "input_dataset": "youruser/your-images-dataset",
            "matcher": "exhaustive", "dense": True, "mesher": "poisson",
            "gpu_minutes_estimate": 180,
        },
    },
    "llm-serve": {
        "job_id": "my-llm-001",
        "profile": "llm-serve",
        "params": {
            "model": "qwen2.5:14b", "api_key": "change-me-long-random",
            "num_ctx": 65536, "idle_shutdown_minutes": 30,
            "gpu_minutes_estimate": 60,
        },
    },
    "workbench": {
        "job_id": "my-experiment-001",
        "profile": "workbench",
        "params": {"command": "nvidia-smi", "gpu_minutes_estimate": 15},
    },
}

NOTES = {
    "photogrammetry": "Input: a Kaggle dataset with an images/ folder. Attach it under Add Input, or the job downloads it by slug. Enable Settings -> Accelerator -> GPU T4 x2 first.",
    "llm-serve": "Enable GPU T4 x2. After ~5 min the output cell prints endpoint.json with the base_url - use it from any OpenAI-compatible client with your X-API-Key.",
    "workbench": "Edit command to anything. GPU T4 x2 optional (set by the run stage).",
}


def nb_for(profile):
    job = JOB_CELLS[profile]
    job_src = "# EDIT ME - your job definition\nJOB = " + json.dumps(job, indent=4).replace("true", "True").replace("false", "False")
    boot_src = BOOTSTRAP.replace("{bundle}", connector.bundle(profile))
    return {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                     "language_info": {"name": "python"}},
        "cells": [
            {"cell_type": "markdown", "metadata": {},
             "source": [f"# KaggleCompute - {profile}\n", "\n", NOTES[profile]]},
            {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
             "source": [job_src]},
            {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
             "source": [boot_src]},
        ],
    }


for profile in JOB_CELLS:
    out = ROOT / "notebooks" / f"kagglecompute-{profile}.ipynb"
    out.write_text(json.dumps(nb_for(profile), indent=1))
    json.loads(out.read_text())  # validate
    print("wrote", out)
