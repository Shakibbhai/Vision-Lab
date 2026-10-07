from __future__ import annotations

from fastapi import FastAPI

from app.core.lifecycle import create_lifespan
from app.db.session import ping_db
from app.views import VERSIONED_ROUTERS, analyzer


def create_app() -> FastAPI:
    app = FastAPI(title="CCTV Capture API", version="0.1.0", lifespan=create_lifespan())

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/health/db")
    async def health_db():
        ok = await ping_db()
        return {"database": "ok" if ok else "unreachable"}

    for router in VERSIONED_ROUTERS:
        app.include_router(router, prefix="/api")

    return app


app = create_app()
