from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
VIDEOS_DIR = DATA_DIR / "videos"
REPORTS_DIR = DATA_DIR / "reports"
HEATMAPS_DIR = DATA_DIR / "heatmaps"
TRACKING_DIR = DATA_DIR / "tracking"

for directory in (DATA_DIR, VIDEOS_DIR, REPORTS_DIR, HEATMAPS_DIR, TRACKING_DIR):
    directory.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    app_name: str = "Balonmano IA"
    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR / 'balonmano.db'}"

    # Detection model. Any YOLOv8 checkpoint works; the default pretrained
    # COCO weights already include "person" (id 0) and "sports ball" (id 32),
    # which is enough to bootstrap player + ball detection without training
    # a custom model first. Swap this for a fine-tuned handball-specific
    # checkpoint later (see docs/ROADMAP.md) to improve ball recall and to
    # add classes like goal posts or referees.
    yolo_model_path: str = "yolov8n.pt"
    detection_confidence: float = 0.25

    # Frame sampling: analysing every frame of a full training session is
    # unnecessary and slow. We run detection at this target FPS and
    # interpolate tracks in between.
    analysis_target_fps: float = 6.0

    cors_origins: list[str] = ["*"]

    model_config = SettingsConfigDict(env_prefix="BALONMANO_")


settings = Settings()
