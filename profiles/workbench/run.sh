#!/usr/bin/env bash
# KaggleCompute workbench profile - arbitrary command runner.
set -euo pipefail
STAGE="$1"; WORK="$2"
P() { python3 -c "import json,os;print(json.loads(os.environ['KC_PARAMS']).get('$1','${2:-}'))"; }

case "$STAGE" in
  fetch)
    DS="$(P input_dataset None)"
    if [ "$DS" != "None" ]; then
      kaggle datasets download -d "$DS" -p "$WORK/input" --unzip
    fi
    nvidia-smi || true
    ;;
  run)
    mkdir -p "$WORK/output"
    cd "$WORK"
    bash -c "$(P command)"
    ;;
  *) echo "unknown stage: $STAGE" >&2; exit 1 ;;
esac
