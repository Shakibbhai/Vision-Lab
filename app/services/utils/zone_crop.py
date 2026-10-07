from __future__ import annotations

from typing import Any

from app.services.utils.zone_geometry import region_points_from_polygon


def crop_image_to_zone(
    image: Any,
    zone: Any,
) -> Any:
    """Cropped the provided image array to the bounding box of the given zone.
    
    If OpenCV or Numpy is unavailable, or the crop is invalid, the original
    image is returned.
    """
    if image is None:
        return image

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return image  # Fallback gracefully if CV2 is missing

    try:
        frame_h, frame_w = image.shape[:2]
        if frame_h <= 0 or frame_w <= 0:
            return image

        polygon = getattr(zone, "polygon", None)
        if not polygon:
            return image

        polygons = region_points_from_polygon(
            polygon,
            frame_w,
            frame_h,
            use_default_on_invalid=False,
        )
        if not polygons:
            return image

        all_points = [point for poly in polygons if len(poly) >= 3 for point in poly]
        if len(all_points) < 3:
            return image

        arr = np.array(all_points, dtype=np.int32)
        x, y, w, h = cv2.boundingRect(arr)
        
        # Add a tiny bit of padding if possible, keeping within bounds
        padding = max(10, int(min(w, h) * 0.05))
        x_min = max(0, x - padding)
        y_min = max(0, y - padding)
        x_max = min(frame_w, x + w + padding)
        y_max = min(frame_h, y + h + padding)

        cropped = image[y_min:y_max, x_min:x_max]
        if cropped.size == 0:
            return image

        return cropped
    except Exception:
        # Avoid crashing the stream if something goes wrong with cropping
        return image
