import { useEffect, useRef, useState } from "react";
import { fetchStreamPreview } from "../api/client";
import type { PixelCorner } from "../types";

const CORNER_LABELS = [
  "Esquina superior izquierda",
  "Esquina superior derecha",
  "Esquina inferior derecha",
  "Esquina inferior izquierda",
];
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
}: {
  /** Receives the 4 corners once they're all set, or undefined otherwise.
   * `incomplete` is true when calibration is enabled but not finished. */
  onChange: (corners: PixelCorner[] | undefined, incomplete: boolean) => void;
  videoFile?: File | null;
  streamUrl?: string;
}) {
  const [enabled, setEnabled] = useState(false);
  const [corners, setCorners] = useState<Corners>(EMPTY);
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [frameSize, setFrameSize] = useState<{ w: number; h: number } | null>(null);
  const [loadingFrame, setLoadingFrame] = useState(false);
  const [frameError, setFrameError] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);

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
    } catch (e) {
      setFrameError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingFrame(false);
    }
  }

  function handleImageClick(e: React.MouseEvent<HTMLDivElement>) {
    const img = imgRef.current;
    if (!img || !frameSize) return;
    const index = corners.findIndex((c) => c === null);
    if (index === -1) return;
    const rect = img.getBoundingClientRect();
    const x = Math.round(((e.clientX - rect.left) / rect.width) * frameSize.w);
    const y = Math.round(((e.clientY - rect.top) / rect.height) * frameSize.h);
    setAndEmit(corners.map((c, i) => (i === index ? { x, y } : c)));
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
        Marca las 4 esquinas de la pista tal y como se ven desde tu cámara fija. Sin calibrar, la
        app asume que la cámara encuadra la pista completa de borde a borde, lo que puede
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
              <div style={{ display: "flex", gap: 8 }}>
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
              <p className="helper-text">
                {nextIndex === -1
                  ? "¡Listo! Las 4 esquinas están marcadas."
                  : `Haz clic en: ${CORNER_LABELS[nextIndex].toLowerCase()} (${nextIndex + 1}/4). ` +
                    "Arriba = una línea de gol, abajo = la otra."}
              </p>
              <div className="calibration-canvas" onClick={handleImageClick}>
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
            {CORNER_LABELS.map((label, i) => (
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
