# All-in-one image for cloud deployment: dashboard + API + analysis worker in
# a single container and a single URL (Hugging Face Spaces, Render, Railway,
# Fly.io, any VPS...). For local multi-container setups see docker-compose.yml.

# --- 1. Build the dashboard -------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Empty API URL = same origin: the API is served by the same container.
ENV VITE_API_URL=""
RUN npm run build

# --- 2. Backend + analysis --------------------------------------------------
FROM python:3.11-slim

# opencv/ultralytics runtime libs; ffmpeg for video decoding and audio sync.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch: cloud free tiers have no GPU, and the CUDA build would
# add several GB to the image for nothing.
RUN pip install --no-cache-dir \
        torch==2.4.1 torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cpu
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the YOLO weights into the image so the first analysis doesn't
# depend on downloading them at runtime.
RUN mkdir -p /app/weights && cd /app/weights \
    && python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" \
    && ls -la /app/weights

COPY backend/app ./app
COPY --from=frontend /frontend/dist ./static

# Hugging Face Spaces runs containers as uid 1000; make everything it needs
# to write (data, caches) owned by that user.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown -R app:app /data /app
USER app

ENV BALONMANO_DATA_DIR=/data \
    BALONMANO_FRONTEND_DIR=/app/static \
    BALONMANO_YOLO_MODEL_PATH=/app/weights/yolov8n.pt \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=7860

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
