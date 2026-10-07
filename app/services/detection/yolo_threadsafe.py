from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def _fallback_threading_locked() -> Callable[[Callable[P, R]], Callable[P, R]]:
    lock = threading.Lock()

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with lock:
                return func(*args, **kwargs)

        return wrapped

    return decorator


def build_locked_callable(func: Callable[P, R]) -> Callable[P, R]:
    """Wrap a callable with Ultralytics ThreadingLocked when available."""
    try:
        from ultralytics.utils import ThreadingLocked  # type: ignore

        decorator = ThreadingLocked()
    except Exception as exc:  # noqa: BLE001
        logger.debug("ThreadingLocked unavailable, using local lock fallback: %s", exc)
        decorator = _fallback_threading_locked()
    return decorator(func)

