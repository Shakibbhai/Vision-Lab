#!/bin/bash
# Supervisor wrapper: Next.js production server on 127.0.0.1:13000, exposed by Caddy on port 10100

utils=/opt/supervisor-scripts/utils
. "${utils}/logging.sh"
. "${utils}/cleanup_generic.sh"
. "${utils}/environment.sh"
. "${utils}/exit_portal.sh" "VisionLab"

. /opt/nvm/nvm.sh
cd /workspace/Vision-Lab/frontend
export NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
pty npx next start -H 127.0.0.1 -p 13000 2>&1
