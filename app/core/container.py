from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import fetcher_enabled
from app.services.analytics.analytics_engine import AnalyticsEngine
from app.services.core.ingestion import StreamIngestionManager
from app.services.core.reconstruction import ReconstructionService


@dataclass(slots=True)
class ServiceContainer:
    ingestion_manager: StreamIngestionManager | None = None
    analytics_engine: AnalyticsEngine | None = None
    reconstruction_service: ReconstructionService | None = None


def build_service_container(session_factory: async_sessionmaker) -> ServiceContainer:
    return ServiceContainer(
        ingestion_manager=StreamIngestionManager(session_factory) if fetcher_enabled() else None,
        analytics_engine=AnalyticsEngine(session_factory) if fetcher_enabled() else None,
        reconstruction_service=ReconstructionService(session_factory) if fetcher_enabled() else None,
    )
