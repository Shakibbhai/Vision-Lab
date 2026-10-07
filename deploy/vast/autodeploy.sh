#!/bin/bash
# Run every minute by cron: pull new commits from origin/main and restart only what changed.
set -euo pipefail

exec 9>/tmp/visionlab-autodeploy.lock
flock -n 9 || exit 0

REPO=/workspace/Vision-Lab
cd "$REPO"

git fetch -q origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0

echo "$(date -Is) deploying ${LOCAL:0:7} -> ${REMOTE:0:7}"
CHANGED=$(git diff --name-only "$LOCAL" "$REMOTE")
git merge -q --ff-only origin/main

if grep -q '^requirements.txt$' <<<"$CHANGED"; then
    source /venv/main/bin/activate
    uv pip install -q -r requirements.txt
fi

if grep -q '^deploy/vast/' <<<"$CHANGED"; then
    bash "$REPO/deploy/vast/install.sh"
fi

if grep -q '^frontend/' <<<"$CHANGED"; then
    . /opt/nvm/nvm.sh
    cd "$REPO/frontend"
    if grep -qE '^frontend/package(-lock)?\.json$' <<<"$CHANGED"; then
        npm ci --no-audit --no-fund
    fi
    NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run build
    cd "$REPO"
    supervisorctl restart visionlab-frontend
fi

if grep -qvE '^(frontend/|deploy/|README\.md$)' <<<"$CHANGED"; then
    supervisorctl restart visionlab-backend
fi

echo "$(date -Is) deployed ${REMOTE:0:7}"
