"""Player and ball detection on top of a YOLOv8 model.

We reuse the standard COCO classes that already ship with the pretrained
YOLOv8 checkpoints: class 0 ("person") for players/referees and class 32
("sports ball") for the ball. This gets a working pipeline off the ground
without needing a custom-labelled handball dataset on day one. See
docs/ROADMAP.md for how to swap in a fine-tuned handball-specific model
(better ball recall in clutter, goalkeeper/referee separation, jersey-color
team assignment, etc.) without touching the rest of the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.config import settings

COCO_PERSON_CLASS = 0
COCO_BALL_CLASS = 32


@dataclass
class Detection:
    xyxy: tuple[float, float, float, float]
    confidence: float
    class_id: int

    @property
    def anchor_point(self) -> tuple[float, float]:
        """Ground-contact point used for court-plane mapping: bottom-center
        of the bounding box for people, centroid for the ball."""
        x1, y1, x2, y2 = self.xyxy
        if self.class_id == COCO_PERSON_CLASS:
            return ((x1 + x2) / 2, y2)
        return ((x1 + x2) / 2, (y1 + y2) / 2)


@dataclass
class FrameDetections:
    persons: list[Detection]
    balls: list[Detection]


@lru_cache(maxsize=1)
def _load_model():
    from ultralytics import YOLO

    return YOLO(settings.yolo_model_path)


class Detector:
    """Thin wrapper so the rest of the app doesn't import ultralytics directly."""

    def __init__(self, confidence: float | None = None):
        self.confidence = confidence if confidence is not None else settings.detection_confidence

    def warm_up(self) -> None:
        """Loads the model and runs one throwaway inference, so the first real
        frame isn't delayed by several seconds (model load, CUDA init)."""
        self.detect(np.zeros((360, 640, 3), dtype=np.uint8))

    def detect(self, frame: np.ndarray) -> FrameDetections:
        model = _load_model()
        results = model.predict(
            frame,
            conf=self.confidence,
            classes=[COCO_PERSON_CLASS, COCO_BALL_CLASS],
            verbose=False,
        )[0]

        persons: list[Detection] = []
        balls: list[Detection] = []
        boxes = results.boxes
        if boxes is None:
            return FrameDetections(persons=persons, balls=balls)

        for box in boxes:
            cls_id = int(box.cls[0])
            det = Detection(
                xyxy=tuple(float(v) for v in box.xyxy[0].tolist()),
                confidence=float(box.conf[0]),
                class_id=cls_id,
            )
            if cls_id == COCO_PERSON_CLASS:
                persons.append(det)
            elif cls_id == COCO_BALL_CLASS:
                balls.append(det)

        return FrameDetections(persons=persons, balls=balls)
