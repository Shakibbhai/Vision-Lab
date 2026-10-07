"""
Pre-download all DeepFace models required by VisionLab.

Run once before `docker compose up`:
    docker compose run --rm --no-deps backend python /app/scripts/download_deepface_models.py
"""

import numpy as np

print("=== VisionLab DeepFace model pre-downloader ===")
print("Models will be saved to /root/.deepface/weights/ (mounted at ./data/deepface/)\n")

# Dummy BGR image large enough for all models
dummy = np.zeros((224, 224, 3), dtype=np.uint8)

from deepface import DeepFace  # noqa: E402  imported after numpy to surface errors early

# --- Facial expression models (emotion + age + gender) ---
print("[1/2] Downloading emotion / age / gender models...")
DeepFace.analyze(
    img_path=dummy,
    actions=["emotion", "age", "gender"],
    enforce_detection=False,
    detector_backend="skip",
    silent=False,
)
print("      Done.\n")

# --- ArcFace (face recognition embeddings) ---
print("[2/2] Downloading ArcFace model...")
DeepFace.represent(
    img_path=dummy,
    model_name="ArcFace",
    detector_backend="skip",
    enforce_detection=False,
)
print("      Done.\n")

print("All DeepFace models downloaded successfully.")
print("You can now run: docker compose up -d")
