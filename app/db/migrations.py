from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection

from app.db import models as _models  # noqa: F401
from app.db.base import Base

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_column(sync_conn, table_name: str, column_name: str) -> bool:
    """Check column existence via synchronous inspector (called via run_sync)."""
    inspector = inspect(sync_conn)
    columns = inspector.get_columns(table_name)
    return any(col["name"] == column_name for col in columns)


async def _ensure_column(
    conn: AsyncConnection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    """Idempotently add a column if it doesn't already exist."""
    # Validate identifiers to prevent SQL injection — only allow safe chars
    for identifier in (table_name, column_name):
        if not identifier.replace("_", "").isalnum():
            raise ValueError(f"Unsafe SQL identifier: {identifier!r}")

    exists = await conn.run_sync(
        lambda sync_conn: _has_column(sync_conn, table_name, column_name)
    )
    if exists:
        logger.debug("Column %s.%s already exists, skipping.", table_name, column_name)
        return

    # Identifiers can't be parameterized in SQLAlchemy — safe after validation above
    await conn.execute(
        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
    )
    logger.info("Added column %s.%s.", table_name, column_name)


# ---------------------------------------------------------------------------
# Individual migrations — one function, one responsibility
# ---------------------------------------------------------------------------

async def migration_01_initial_schema(conn: AsyncConnection) -> None:
    """Create all tables defined in SQLAlchemy metadata."""
    await conn.run_sync(Base.metadata.create_all)


async def migration_02_footfall_event_type(conn: AsyncConnection) -> None:
    """Add event_type column to footfall_events."""
    await _ensure_column(
        conn,
        "footfall_events",
        "event_type",
        "VARCHAR(20) NOT NULL DEFAULT 'entry'",
    )


async def migration_03_zone_capacity(conn: AsyncConnection) -> None:
    """Add capacity and expected_wait_time_sec to zones."""
    await _ensure_column(conn, "zones", "capacity", "INTEGER")
    await _ensure_column(conn, "zones", "expected_wait_time_sec", "INTEGER")


async def migration_04_camera_soft_delete(conn: AsyncConnection) -> None:
    """Add is_deleted column to cameras."""
    await _ensure_column(
        conn,
        "cameras",
        "is_deleted",
        "BOOLEAN NOT NULL DEFAULT 0",
    )


# ---------------------------------------------------------------------------
# Migration registry — ordered, versioned, explicit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    apply: Callable[[AsyncConnection], Awaitable[None]]


MIGRATIONS: list[Migration] = [
    Migration(1, "Initial schema", migration_01_initial_schema),
    Migration(2, "Add footfall_events.event_type", migration_02_footfall_event_type),
    Migration(3, "Add zones.capacity and expected_wait_time_sec", migration_03_zone_capacity),
    Migration(4, "Add cameras.is_deleted", migration_04_camera_soft_delete),
]

# Sanity-check: versions must be sequential starting at 1
assert [m.version for m in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1)), (
    "MIGRATIONS list has gaps or duplicates — fix version numbers."
)


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

_CREATE_MIGRATIONS_TABLE = text("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        version INTEGER NOT NULL UNIQUE,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")

_GET_CURRENT_VERSION = text(
    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
)

_INSERT_VERSION = text(
    "INSERT INTO schema_migrations (version) VALUES (:version)"
)


async def apply_migrations(engine: AsyncEngine) -> None:
    """Apply any pending migrations in order, each in its own transaction."""

    # Ensure the tracking table exists (idempotent)
    async with engine.begin() as conn:
        await conn.execute(_CREATE_MIGRATIONS_TABLE)

    # Read current version outside the per-migration transaction
    async with engine.connect() as conn:
        result = await conn.execute(_GET_CURRENT_VERSION)
        current_version: int = result.scalar_one()

    logger.info("Database is at migration version %d.", current_version)

    for migration in MIGRATIONS:
        if migration.version <= current_version:
            continue

        logger.info(
            "Applying migration %d: %s …", migration.version, migration.description
        )
        # Each migration gets its own transaction — failure rolls back only that step
        async with engine.begin() as conn:
            await migration.apply(conn)
            await conn.execute(_INSERT_VERSION, {"version": migration.version})

        logger.info("Migration %d applied successfully.", migration.version)

    logger.info("All migrations up to date.")