#!/bin/bash
# Idempotent: registers supervisor services, the Caddy portal entry and the autodeploy cron job.
set -euo pipefail

DEPLOY=/workspace/Vision-Lab/deploy/vast
chmod +x "$DEPLOY"/*.sh

for svc in backend frontend; do
    cat > "/etc/supervisor/conf.d/visionlab-${svc}.conf" <<EOF
[program:visionlab-${svc}]
environment=PROC_NAME="%(program_name)s"
command=${DEPLOY}/${svc}.sh
autostart=true
autorestart=true
startsecs=5
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=20
stdout_logfile=/dev/stdout
redirect_stderr=true
stdout_events_enabled=true
stdout_logfile_maxbytes=0
stdout_logfile_backups=0
EOF
done

# Frontend behind the token-authed Caddy edge: public port 10100 -> 127.0.0.1:13000
/venv/main/bin/python - <<'EOF'
import yaml
path = "/etc/portal.yaml"
d = yaml.safe_load(open(path)) or {"applications": {}}
d.setdefault("applications", {})["VisionLab"] = {
    "hostname": "localhost",
    "external_port": 10100,
    "internal_port": 13000,
    "open_path": "/dashboard",
    "name": "VisionLab",
}
yaml.safe_dump(d, open(path, "w"), sort_keys=False)
EOF
rm -f /tmp/supervisor-skip/VisionLab

cat > /etc/cron.d/visionlab-autodeploy <<EOF
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
* * * * * root ${DEPLOY}/autodeploy.sh >> /var/log/portal/visionlab-autodeploy.log 2>&1
EOF
chmod 644 /etc/cron.d/visionlab-autodeploy

supervisorctl reread >/dev/null
supervisorctl update
supervisorctl restart caddy >/dev/null
