import { useEffect, useRef, useState } from "react";
import { fetchStreamPreview, suggestCorners } from "../api/client";
import type { CourtRegion, PixelCorner } from "../types";

// Top/bottom edges are the sidelines (long sides), left/right the goal lines
// (or the center line for a half), matching the homography in
// backend/app/court.py (region_world_corners).
const CORNER_LABELS: Record<CourtRegion, string[]> = {
  full: [
    "Esquina superior izquierda",
    "Esquina superior derecha",
    "Esquina inferior derecha",
    "Esquina inferior izquierda",
  ],
  left: [
    "Esquina superior de la línea de gol izquierda",
    "Extremo superior de la línea central",
    "Extremo inferior de la línea central",
    "Esquina inferior de la línea de gol izquierda",
  ],
  right: [
    "Extremo superior de la línea central",
    "Esquina superior de la línea de gol derecha",
    "Esquina inferior de la línea de gol derecha",
    "Extremo inferior de la línea central",
  ],
};

const REGION_HINT: Record<CourtRegion, string> = {
  full: "Los bordes de arriba y abajo son las bandas (lados largos); izquierda y derecha, las líneas de gol.",
  left: "Esta cámara cubre la mitad izquierda: sus esquinas son las de la portería izquierda y los extremos de la línea central.",
  right: "Esta cámara cubre la mitad derecha: sus esquinas son los extremos de la línea central y las de la portería derecha.",
};
const CORNER_SHORT = ["1", "2", "3", "4"];

type Corners = (PixelCorner | null)[];

const EMPTY: Corners = [null, null, null, null];

/** Grabs a frame (around the 1s mark) from a local video file as a JPEG object URL. */
function frameFromVideoFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const src = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.src = src;

    const cleanup = () => URL.revokeObjectURL(src);
    video.onerror = () => {
      cleanup();
      reject(new Error("El navegador no puede leer este formato de vídeo"));
    };
    video.onloadedmetadata = () => {
      video.currentTime = Math.min(1, (video.duration || 0) / 2);
    };
    video.onseeked = () => {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d")?.drawImage(video, 0, 0);
      canvas.toBlob((blob) => {
        cleanup();
        if (blob) resolve(URL.createObjectURL(blob));
        else reject(new Error("No se pudo extraer un fotograma del vídeo"));
      }, "image/jpeg");
    };
  });
}

export default function CalibrationFields({
  onChange,
  videoFile,
  streamUrl,
  region = "full",
}: {
  /** Receives the 4 corners once they're all set, or undefined otherwise.
   * `incomplete` is true when calibration is enabled but not finished. */
  onChange: (corners: PixelCorner[] | undefined, incomplete: boolean) => void;
  videoFile?: File | null;
  streamUrl?: string;
  /** Part of the court this camera films (multi-camera sessions). */
  region?: CourtRegion;
}) {
  const [enabled, setEnabled] = useState(false);
  const [corners, setCorners] = useState<Corners>(EMPTY);
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [frameSize, setFrameSize] = useState<{ w: number; h: number } | null>(null);
  const [loadingFrame, setLoadingFrame] = useState(false);
  const [frameError, setFrameError] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [detectMessage, setDetectMessage] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  // A drag ends with a click event on the canvas; ignore that one.
  const justDragged = useRef(false);

  function emit(next: Corners, isEnabled: boolean) {
    const complete = next.every((c): c is PixelCorner => c !== null);
    if (!isEnabled) onChange(undefined, false);
    else if (complete) onChange(next as PixelCorner[], false);
    else onChange(undefined, true);
  }

  function setAndEmit(next: Corners) {
    setCorners(next);
    emit(next, enabled);
  }

  function toggle(next: boolean) {
    setEnabled(next);
    emit(corners, next);
  }

  // A different video means a different framing: previous clicks no longer apply.
  useEffect(() => {
    setFrameUrl(null);
    setFrameSize(null);
    setFrameError(null);
  }, [videoFile, streamUrl]);

  // Release the previous frame's object URL whenever it's replaced/cleared.
  useEffect(
    () => () => {
      if (frameUrl) URL.revokeObjectURL(frameUrl);
    },
    [frameUrl],
  );

  async function loadFrame() {
    setLoadingFrame(true);
    setFrameError(null);
    try {
      let url: string;
      if (videoFile) url = await frameFromVideoFile(videoFile);
      else if (streamUrl) url = URL.createObjectURL(await fetchStreamPreview(streamUrl));
      else throw new Error("Selecciona primero un vídeo o indica la URL del stream");
      setFrameUrl(url);
      setAndEmit(EMPTY);
      await detectCorners(url);
    } catch (e) {
      setFrameError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingFrame(false);
    }
  }

  /** Asks the backend to find the court lines and pre-place the 4 corners. */
  async function detectCorners(url: string | null = frameUrl) {
    if (!url) return;
    setDetecting(true);
    setDetectMessage(null);
    try {
      const blob = await (await fetch(url)).blob();
      setAndEmit(await suggestCorners(blob));
      setDetectMessage("Esquinas detectadas automáticamente: revísalas y arrastra las que no encajen.");
    } catch (e) {
      setDetectMessage(
        `${e instanceof Error ? e.message : String(e)}`.replace(/\.$/, "") +
          ". Haz clic en las esquinas manualmente.",
      );
    } finally {
      setDetecting(false);
    }
  }

  function toImageCoords(clientX: number, clientY: number): PixelCorner | null {
    const img = imgRef.current;
    if (!img || !frameSize) return null;
    const rect = img.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * frameSize.w;
    const y = ((clientY - rect.top) / rect.height) * frameSize.h;
    return {
      x: Math.round(Math.min(frameSize.w, Math.max(0, x))),
      y: Math.round(Math.min(frameSize.h, Math.max(0, y))),
    };
  }

  function handleImageClick(e: React.MouseEvent<HTMLDivElement>) {
    if (justDragged.current) {
      justDragged.current = false;
      return;
    }
    const index = corners.findIndex((c) => c === null);
    const point = toImageCoords(e.clientX, e.clientY);
    if (index === -1 || !point) return;
    setAndEmit(corners.map((c, i) => (i === index ? point : c)));
  }

  function startDrag(index: number, e: React.PointerEvent) {
    e.preventDefault();
    e.stopPropagation();
    canvasRef.current?.setPointerCapture(e.pointerId);
    setDragIndex(index);
  }

  function handlePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (dragIndex === null) return;
    const point = toImageCoords(e.clientX, e.clientY);
    if (point) setAndEmit(corners.map((c, i) => (i === dragIndex ? point : c)));
  }

  function endDrag(e: React.PointerEvent<HTMLDivElement>) {
    if (dragIndex === null) return;
    canvasRef.current?.releasePointerCapture(e.pointerId);
    setDragIndex(null);
    justDragged.current = true;
  }

  function undo() {
    const lastSet = corners.map((c) => c !== null).lastIndexOf(true);
    if (lastSet === -1) return;
    setAndEmit(corners.map((c, i) => (i === lastSet ? null : c)));
  }

  function updateNumeric(index: number, axis: "x" | "y", raw: string) {
    const value = Number(raw);
    const current = corners[index] ?? { x: 0, y: 0 };
    setAndEmit(corners.map((c, i) => (i === index ? { ...current, [axis]: value } : c)));
  }

  const labels = CORNER_LABELS[region];
  const nextIndex = corners.findIndex((c) => c === null);
  const placed = corners.filter((c): c is PixelCorner => c !== null);
  const canLoadFrame = Boolean(videoFile || streamUrl);
  const markerRadius = frameSize ? Math.max(frameSize.w, frameSize.h) / 120 : 6;

  return (
    <div className="form-group">
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => toggle(e.target.checked)}
          style={{ marginRight: 8 }}
        />
        Calibrar cámara (recomendado para distancias/velocidades precisas)
      </label>
      <p className="helper-text">
        Marca las 4 esquinas {region === "full" ? "de la pista" : "de la media pista que cubre"} tal
        y como se ven desde tu cámara fija. Sin calibrar, la app asume que la cámara encuadra{" "}
        {region === "full" ? "la pista completa" : "su mitad"} de borde a borde, lo que puede
        distorsionar las medidas en los laterales.
      </p>

      {enabled && (
        <>
          <div className="calibration-toolbar">
            <button
              type="button"
              className="btn"
              onClick={loadFrame}
              disabled={!canLoadFrame || loadingFrame}
            >
              {loadingFrame ? "Cargando imagen…" : frameUrl ? "Recargar imagen" : "Marcar sobre la imagen"}
            </button>
            {frameUrl && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" className="btn" onClick={() => detectCorners()} disabled={detecting}>
                  {detecting ? "Detectando…" : "Detectar esquinas"}
                </button>
                <button type="button" className="btn" onClick={undo} disabled={placed.length === 0}>
                  Deshacer
                </button>
                <button type="button" className="btn" onClick={() => setAndEmit(EMPTY)} disabled={placed.length === 0}>
                  Reiniciar
                </button>
              </div>
            )}
          </div>
          {!canLoadFrame && (
            <p className="helper-text">
              Selecciona el vídeo (o escribe la URL del stream) para poder marcar las esquinas con
              clics, o introdúcelas a mano abajo.
            </p>
          )}
          {frameError && <p className="error-text">{frameError} — puedes introducir las coordenadas a mano.</p>}

          {frameUrl && (
            <>
              {detectMessage && <p className="helper-text">{detectMessage}</p>}
              <p className="helper-text">
                {nextIndex === -1
                  ? "Las 4 esquinas están marcadas. Puedes arrastrarlas para ajustarlas."
                  : `Haz clic en: ${labels[nextIndex].toLowerCase()} (${nextIndex + 1}/4). ${REGION_HINT[region]}`}
              </p>
              <div
                ref={canvasRef}
                className={`calibration-canvas${dragIndex !== null ? " dragging" : ""}`}
                onClick={handleImageClick}
                onPointerMove={handlePointerMove}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
              >
                <img
                  ref={imgRef}
                  src={frameUrl}
                  alt="Fotograma de la cámara para calibrar"
                  draggable={false}
                  onLoad={(e) =>
                    setFrameSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
                  }
                />
                {frameSize && (
                  <svg viewBox={`0 0 ${frameSize.w} ${frameSize.h}`} preserveAspectRatio="none">
                    {placed.length > 1 && (
                      <polygon
                        points={placed.map((c) => `${c.x},${c.y}`).join(" ")}
                        fill={placed.length === 4 ? "rgba(47,191,113,0.18)" : "none"}
                        stroke="#2fbf71"
                        strokeWidth={markerRadius / 3}
                      />
                    )}
                    {corners.map(
                      (c, i) =>
                        c && (
                          <g key={i}>
                            <circle cx={c.x} cy={c.y} r={markerRadius} fill="#2fbf71" stroke="#05170d" />
                            {/* Invisible, larger hit area so corners are easy to grab. */}
                            <circle
                              className="corner-handle"
                              cx={c.x}
                              cy={c.y}
                              r={markerRadius * 2.5}
                              fill="transparent"
                              onPointerDown={(e) => startDrag(i, e)}
                            />
                            <text
                              x={c.x + markerRadius * 1.4}
                              y={c.y - markerRadius * 1.4}
                              fill="#ffffff"
                              fontSize={markerRadius * 2.6}
                              fontWeight={700}
                            >
                              {CORNER_SHORT[i]}
                            </text>
                          </g>
                        ),
                    )}
                  </svg>
                )}
              </div>
            </>
          )}

          <div className="corner-grid" style={{ marginTop: 12 }}>
            {labels.map((label, i) => (
              <div key={label}>
                <label style={{ fontWeight: 400 }}>
                  {i + 1}. {label}
                </label>
                <div className="corner-field">
                  <input
                    type="number"
                    placeholder="x (px)"
                    value={corners[i]?.x ?? ""}
                    onChange={(e) => updateNumeric(i, "x", e.target.value)}
                  />
                  <input
                    type="number"
                    placeholder="y (px)"
                    value={corners[i]?.y ?? ""}
                    onChange={(e) => updateNumeric(i, "y", e.target.value)}
                  />
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
