from __future__ import annotations

from fastapi import Request

from app.core.container import ServiceContainer


def get_service_container(request: Request) -> ServiceContainer:
    services = getattr(request.app.state, "services", None)
    if isinstance(services, ServiceContainer):
        return services
    return ServiceContainer()
