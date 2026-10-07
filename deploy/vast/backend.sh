#!/bin/bash
# Supervisor wrapper: FastAPI backend on 127.0.0.1:8000 (reached via the frontend's /api/backend proxy)

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"

source /venv/main/bin/activate
cd /workspace/Vision-Lab
# deepface pulls in TensorFlow, which grabs all VRAM by default and starves the PyTorch models
export TF_FORCE_GPU_ALLOW_GROWTH=true
pty python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 2>&1
