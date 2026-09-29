import asyncio

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas import CornerSuggestionOut, PixelCorner
from app.vision.court_lines import suggest_court_corners

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

router = APIRouter(prefix="/calibration", tags=["calibration"])

MAX_IMAGE_BYTES = 15 * 1024 * 1024


@router.post("/suggest", response_model=CornerSuggestionOut)
async def suggest_corners(image: UploadFile = File(...)):
    """Suggests the 4 court corners on a camera frame (JPEG/PNG) by
    detecting the painted court lines. The user reviews/adjusts them."""
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Imagen demasiado grande")
    if cv2 is None:
        raise HTTPException(status_code=500, detail="opencv-python no está instalado")

    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail="No se pudo leer la imagen")

    corners = await asyncio.to_thread(suggest_court_corners, frame)
    if corners is None:
        raise HTTPException(
            status_code=422,
            detail="No se detectaron las líneas de la pista; marca las esquinas a mano.",
        )
    return CornerSuggestionOut(corners=[PixelCorner(x=round(x), y=round(y)) for x, y in corners])
