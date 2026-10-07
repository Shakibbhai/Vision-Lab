from __future__ import annotations

from typing import Any


DEFAULT_REGION = [(20, 400), (1080, 400), (1080, 360), (20, 360)]
NORMALIZED_COORD_EPSILON = 0.05


def region_points_from_polygon(
    polygon: Any,
    width: int,
    height: int,
    minimum_points: int = 3,
    use_default_on_invalid: bool = True,
) -> list[list[tuple[int, int]]]:
    """Convert persisted polygon payloads into absolute integer region points."""
    fallback = [DEFAULT_REGION.copy()] if use_default_on_invalid else []
    if width <= 0 or height <= 0:
        return fallback

    raw_polygons: list[list[Any]] = []
    
    if isinstance(polygon, dict):
        if "polygons" in polygon and isinstance(polygon["polygons"], list):
            raw_polygons = polygon["polygons"]
        elif "points" in polygon and isinstance(polygon["points"], list):
            raw_polygons = [polygon["points"]]
    elif isinstance(polygon, list):
        if len(polygon) > 0 and isinstance(polygon[0], list):
            raw_polygons = polygon
        else:
            raw_polygons = [polygon]
    else:
        return fallback

    all_regions: list[list[tuple[int, int]]] = []

    for raw_points in raw_polygons:
        if not isinstance(raw_points, list):
            continue

        parsed: list[tuple[float, float]] = []
        for point in raw_points:
            value_x: float
            value_y: float
            if isinstance(point, dict):
                if "x" not in point or "y" not in point:
                    continue
                value_x = float(point["x"])
                value_y = float(point["y"])
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                value_x = float(point[0])
                value_y = float(point[1])
            else:
                continue
            parsed.append((value_x, value_y))

        if len(parsed) < minimum_points:
            continue

        # Treat values that are slightly out of [0, 1] as normalized coordinates.
        is_normalized_01 = all(
            -NORMALIZED_COORD_EPSILON <= x <= 1.0 + NORMALIZED_COORD_EPSILON
            and -NORMALIZED_COORD_EPSILON <= y <= 1.0 + NORMALIZED_COORD_EPSILON
            for x, y in parsed
        )

        # If not 0-1, check if they are in 0-100 range (percentages)
        is_normalized_100 = not is_normalized_01 and all(
            -NORMALIZED_COORD_EPSILON * 100 <= x <= 100.0 + NORMALIZED_COORD_EPSILON * 100
            and -NORMALIZED_COORD_EPSILON * 100 <= y <= 100.0 + NORMALIZED_COORD_EPSILON * 100
            for x, y in parsed
        )

        region: list[tuple[int, int]] = []
        for value_x, value_y in parsed:
            if is_normalized_01:
                nx = min(1.0, max(0.0, value_x))
                ny = min(1.0, max(0.0, value_y))
                px = int(round(nx * (width - 1)))
                py = int(round(ny * (height - 1)))
            elif is_normalized_100:
                nx = min(1.0, max(0.0, value_x / 100.0))
                ny = min(1.0, max(0.0, value_y / 100.0))
                px = int(round(nx * (width - 1)))
                py = int(round(ny * (height - 1)))
            else:
                px = int(round(value_x))
                py = int(round(value_y))
            px = max(0, min(width - 1, px))
            py = max(0, min(height - 1, py))
            region.append((px, py))


        # Preserve order but remove exact duplicates.
        deduped: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for point in region:
            if point in seen:
                continue
            deduped.append(point)
            seen.add(point)

        if len(deduped) >= minimum_points:
            all_regions.append(deduped)

    if not all_regions:
        return fallback
    return all_regions


def line_points_from_polygon(
    polygon: Any,
    width: int,
    height: int,
    line_key: str = "entry_exit_line",
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Extract optional explicit line from a polygon payload."""
    if width <= 0 or height <= 0:
        return None
    if not isinstance(polygon, dict):
        return None

    line_payload = polygon.get(line_key)
    if not isinstance(line_payload, dict):
        return None

    raw_points = line_payload.get("points")
    if not isinstance(raw_points, list) or len(raw_points) < 2:
        return None

    parsed: list[tuple[float, float]] = []
    for point in raw_points[:2]:
        value_x: float
        value_y: float
        if isinstance(point, dict):
            if "x" not in point or "y" not in point:
                continue
            value_x = float(point["x"])
            value_y = float(point["y"])
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            value_x = float(point[0])
            value_y = float(point[1])
        else:
            continue
        parsed.append((value_x, value_y))

    if len(parsed) < 2:
        return None

    is_normalized = all(
        -NORMALIZED_COORD_EPSILON <= x <= 1.0 + NORMALIZED_COORD_EPSILON
        and -NORMALIZED_COORD_EPSILON <= y <= 1.0 + NORMALIZED_COORD_EPSILON
        for x, y in parsed
    )

    points: list[tuple[float, float]] = []
    for value_x, value_y in parsed:
        if is_normalized:
            nx = min(1.0, max(0.0, value_x))
            ny = min(1.0, max(0.0, value_y))
            px = float(nx * (width - 1))
            py = float(ny * (height - 1))
        else:
            px = float(value_x)
            py = float(value_y)
        px = float(max(0.0, min(width - 1, px)))
        py = float(max(0.0, min(height - 1, py)))
        points.append((px, py))

    if len(points) < 2:
        return None

    a, b = points[0], points[1]
    if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6:
        return None
    return (a, b)
