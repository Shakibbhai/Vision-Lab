from __future__ import annotations

import asyncio

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.migrations import apply_migrations

_IS_SQLITE = settings.database_url.startswith("sqlite")

_engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"timeout": 30} if _IS_SQLITE else {},
)

if _IS_SQLITE:
    @event.listens_for(_engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        # Improve concurrent read/write behavior for API + ingestion workers.
        journal_mode = (getattr(settings, "sqlite_journal_mode", "DELETE") or "DELETE").strip().upper()
        if journal_mode not in {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}:
            journal_mode = "DELETE"
        cursor.execute(f"PRAGMA journal_mode={journal_mode}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    await apply_migrations(_engine)


async def ping_db(retries: int = 3, base_delay: float = 0.5) -> bool:
    for attempt in range(retries):
        try:
            async with _engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            if attempt >= retries - 1:
                return False
            await asyncio.sleep(base_delay * (2**attempt))
    return False


def get_engine() -> AsyncEngine:
    return _engine


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
