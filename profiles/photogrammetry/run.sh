#!/usr/bin/env bash
# KaggleCompute photogrammetry profile - stage dispatcher.
# GPU/COLMAP stages run through pipeline.py (pycolmap CUDA wheels);
# the Debian colmap CLI is CPU-only and crashes headless - do not use it.
# Called by run.py as: run.sh <stage> <work_dir>; params in $KC_PARAMS (JSON).
set -euo pipefail
STAGE="$1"; WORK="$2"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P() { python3 -c "import json,os;print(json.loads(os.environ['KC_PARAMS']).get('$1','${2:-}'))"; }
IMAGES="$WORK/images"
OUT="$WORK/output"; mkdir -p "$OUT"

case "$STAGE" in
  fetch)
    # the job's dataset is already mounted at /kaggle/input/<slug> via dataset_sources;
    # fall back to a CLI download for ad-hoc/local runs
    SLUG="$(basename "$(P input_dataset)")"
    SRC=""
    if [ -d "/kaggle/input/$SLUG" ]; then
      SRC="/kaggle/input/$SLUG"; echo "using mounted dataset: $SRC"
    else
      kaggle datasets download -d "$(P input_dataset)" -p "$WORK/input" --unzip
      SRC="$WORK/input"; echo "downloaded dataset to: $SRC"
    fi
    # accept either an images/ folder or a flat pile of images
    if [ -d "$SRC/images" ]; then ln -sfn "$SRC/images" "$IMAGES"; else mkdir -p "$IMAGES"; find "$SRC" -maxdepth 2 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) -exec cp {} "$IMAGES/" \;; fi
    echo "images: $(find -L "$IMAGES" -type f | wc -l)"
    ;;
  install)
    t0=$SECONDS
    pip install -q pycolmap-cuda12 open3d
    echo "pip pycolmap-cuda12 + open3d: took $((SECONDS-t0))s"
    python3 - <<'PY'
import pycolmap
print('has_cuda:', pycolmap.has_cuda if hasattr(pycolmap, 'has_cuda') else 'attr-missing')
PY
    ;;
  extract|match|sfm)
    python3 "$HERE/pipeline.py" "$STAGE" "$WORK"
    ;;
  undistort|stereo|fusion|mesh)
    if [ "$(P dense true)" = "True" ]; then
      python3 "$HERE/pipeline.py" "$STAGE" "$WORK"
    fi
    ;;
  *) echo "unknown stage: $STAGE" >&2; exit 1 ;;
esac
