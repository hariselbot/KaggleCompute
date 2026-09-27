#!/usr/bin/env bash
# KaggleCompute photogrammetry profile - stage implementations.
# Called by run.py as: run.sh <stage> <work_dir>; params in $KC_PARAMS (JSON).
set -euo pipefail
STAGE="$1"; WORK="$2"
P() { python3 -c "import json,os;print(json.loads(os.environ['KC_PARAMS']).get('$1','$2'))"; }
IMAGES="$WORK/images"; DB="$WORK/db.db"; SPARSE="$WORK/sparse"; DENSE="$WORK/dense"
OUT="$WORK/output"; mkdir -p "$OUT"

case "$STAGE" in
  fetch)
    python3 -m kaggle datasets download -d "$(P input_dataset)" -p "$WORK/input" --unzip
    # accept either an images/ folder or a flat pile of images
    if [ -d "$WORK/input/images" ]; then ln -sfn "$WORK/input/images" "$IMAGES"; else mkdir -p "$IMAGES"; find "$WORK/input" -maxdepth 2 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) -exec cp {} "$IMAGES/" \;; fi
    echo "images: $(find -L "$IMAGES" -type f | wc -l)"
    ;;
  install)
    export DEBIAN_FRONTEND=noninteractive
    (apt-get update -qq && apt-get install -y -qq colmap ffmpeg) || (sudo apt-get update -qq && sudo apt-get install -y -qq colmap ffmpeg)
    pip install -q open3d
    colmap -h | head -3
    ;;
  extract)
    colmap feature_extractor --database_path "$DB" --image_path "$IMAGES" \
      --SiftExtraction.use_gpu 1 --SiftExtraction.max_image_size "$(P max_image_size 3200)"
    ;;
  match)
    colmap "$(P matcher exhaustive)_matcher" --database_path "$DB" --SiftMatching.use_gpu 1
    ;;
  sfm)
    mkdir -p "$SPARSE"
    colmap mapper --database_path "$DB" --image_path "$IMAGES" --output_path "$SPARSE"
    cp -r "$SPARSE" "$OUT/sparse"
    ;;
  undistort)
    if [ "$(P dense true)" = "True" ]; then
      colmap image_undistorter --image_path "$IMAGES" --input_path "$SPARSE/0" --output_path "$DENSE" --output_type COLMAP
    fi
    ;;
  stereo)
    if [ "$(P dense true)" = "True" ]; then
      colmap patch_match_stereo --workspace_path "$DENSE" --PatchMatchStereo.geom_consistency true
    fi
    ;;
  fusion)
    if [ "$(P dense true)" = "True" ]; then
      colmap stereo_fusion --workspace_path "$DENSE" \
        --input_type geometric --output_path "$DENSE/fused.ply"
      cp "$DENSE/fused.ply" "$OUT/fused.ply"
    fi
    ;;
  mesh)
    if [ "$(P dense true)" = "True" ]; then
      if [ "$(P mesher poisson)" = "poisson" ]; then
        colmap poisson_mesher --input_path "$DENSE/fused.ply" --output_path "$OUT/mesh.ply"
      else
        colmap delaunay_mesher --input_path "$DENSE" --output_path "$OUT/mesh.ply"
      fi
      ls -la "$OUT"
    fi
    ;;
  *) echo "unknown stage: $STAGE" >&2; exit 1 ;;
esac
