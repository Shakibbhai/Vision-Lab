from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_PERSON_TERMS = {
    "person",
    "people",
    "pedestrian",
    "pedestrians",
    "human",
    "humans",
}


def resolve_person_class_ids(model: Any) -> list[int]:
    names = _extract_model_names(model)
    if not names:
        return [0]

    person_ids = _match_person_ids(names)
    if person_ids:
        return person_ids

    if len(names) == 1:
        only_id = next(iter(names))
        logger.info("No explicit person label found; using the only model class id=%s", only_id)
        return [only_id]

    if 0 in names:
        logger.warning(
            "No explicit person label found in model names; falling back to class id 0 ('%s')",
            names.get(0),
        )
        return [0]

    first_id = sorted(names.keys())[0]
    logger.warning(
        "No explicit person label found in model names; falling back to first class id %s ('%s')",
        first_id,
        names.get(first_id),
    )
    return [first_id]


def _extract_model_names(model: Any) -> dict[int, str]:
    raw_sources = (
        getattr(model, "names", None),
        getattr(getattr(model, "model", None), "names", None),
    )
    for raw in raw_sources:
        names = _normalize_names(raw)
        if names:
            return names
    return {}


def _normalize_names(raw: Any) -> dict[int, str]:
    if raw is None:
        return {}

    names: dict[int, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                class_id = int(key)
            except (TypeError, ValueError):
                continue
            names[class_id] = str(value)
        return names

    if isinstance(raw, (list, tuple)):
        for class_id, value in enumerate(raw):
            names[class_id] = str(value)
        return names

    return {}


def _match_person_ids(names: dict[int, str]) -> list[int]:
    ids: list[int] = []
    for class_id, label in names.items():
        normalized = _normalize_label(label)
        if not normalized:
            continue
        words = set(normalized.split())
        if words.intersection(_PERSON_TERMS):
            ids.append(class_id)
            continue
        if "person" in normalized or "people" in normalized or "pedestrian" in normalized:
            ids.append(class_id)
    return sorted(set(ids))


def _normalize_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
