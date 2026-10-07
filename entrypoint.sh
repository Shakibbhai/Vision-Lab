#!/bin/bash
set -e

WEIGHTS_DIR=/root/.deepface/weights

REQUIRED_WEIGHTS=(
    "arcface_weights.h5"
    "age_model_weights.h5"
    "gender_model_weights.h5"
    "facial_expression_model_weights.h5"
)

needs_download=false
for f in "${REQUIRED_WEIGHTS[@]}"; do
    if [ ! -f "$WEIGHTS_DIR/$f" ]; then
        needs_download=true
        break
    fi
done

if [ "$needs_download" = true ]; then
    echo "=== Downloading DeepFace models (one-time setup) ==="
    python /app/scripts/download_deepface_models.py
    echo "=== Model download complete ==="
fi

exec "$@"
