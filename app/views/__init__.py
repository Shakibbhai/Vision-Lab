from __future__ import annotations

from app.views import (
    analytics,
    analyzer,
    cameras,
    config,
    media,
    monitoring,
    queue_management,
    reconstructions,
    streams,
    faces,
    insights,
)

PUBLIC_ROUTERS = [
    streams.router,
    analytics.router,
    reconstructions.router,
    monitoring.router,
    config.router,
    analyzer.router,
    media.router,
    faces.router,
    insights.router,
]

VERSIONED_ROUTERS = [cameras.router, *PUBLIC_ROUTERS, queue_management.router]
