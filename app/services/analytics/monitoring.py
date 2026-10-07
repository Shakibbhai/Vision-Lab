from __future__ import annotations

import os
import shutil
from datetime import datetime

from app.core.config import settings
from app.db import crud


async def collect_metrics(session) -> dict:
    counts = await crud.count_metrics(session)
    disk = shutil.disk_usage(settings.frames_dir)
    disk_used_pct = round((disk.used / disk.total) * 100, 2) if disk.total else 0.0

    load = None
    if hasattr(os, "getloadavg"):
        load = os.getloadavg()

    alerts: list[str] = []
    if disk_used_pct >= 90:
        alerts.append("Disk usage above 90%")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "counts": counts,
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "used_pct": disk_used_pct,
        },
        "load": load,
        "alerts": alerts,
    }
