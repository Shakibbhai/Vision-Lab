from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from app.core.config import settings


def resolve_rtsp_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "rtsp":
        return url

    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost"}:
        return url
    if not settings.rtsp_internal_host:
        return url

    port = parsed.port or 0
    path = parsed.path or ""
    if path in {"", "/", "/stream"} and port:
        path = f"/stream_{port}"

    out_port = settings.rtsp_internal_port or port
    netloc = settings.rtsp_internal_host
    if out_port:
        netloc = f"{netloc}:{out_port}"

    rebuilt = parsed._replace(netloc=netloc, path=path)
    return urlunparse(rebuilt)
