import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  startBrowserLiveMatch,
  startLiveMatch,
  startMultiCameraLive,
  uploadMatchVideo,
  uploadMultiCameraMatch,
} from "../api/client";
import {
  cameraSupported,
  captureFrame,
  closeCamera,
  currentStream,
  getStatus,
  openCamera,
  startSending,
} from "../camera/browserCamera";
import CalibrationFields from "../components/CalibrationFields";
import DeviceCameraPreview from "../components/DeviceCameraPreview";
import type { CameraSetup, CourtRegion, CourtType } from "../types";

type Mode = "upload" | "live";
/** Live source: this device's camera, or IP cameras by RTSP URL. */
type LiveSource = "device" | "rtsp";

const MAX_CAMERAS = 4;

const REGION_LABEL: Record<CourtRegion, string> = {
  full: "Pista completa",
  left: "Mitad izquierda",
  right: "Mitad derecha",
};

function newCamera(key: number, region: CourtRegion = "full"): CameraSetup {
  return {
    key,
    name: "",
    region,
    file: null,
    streamUrl: "",
    corners: undefined,
    calibrationIncomplete: false,
    autoSync: true,
    offsetS: 0,
  };
}

export default function NewSession() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("upload");
  const [name, setName] = useState("");
  const [courtType, setCourtType] = useState<CourtType>("piso");
  const [cameras, setCameras] = useState<CameraSetup[]>([newCamera(0)]);
  const [liveSource, setLiveSource] = useState<LiveSource>(cameraSupported() ? "device" : "rtsp");
  const [deviceStream, setDeviceStream] = useState<MediaStream | null>(currentStream());
  const [openingCamera, setOpeningCamera] = useState(false);
  const deviceCamera = mode === "live" && liveSource === "device";

  // Leaving the form without starting a session: release the camera (unless
  // it's already feeding a live session).
  useEffect(
    () => () => {
      if (!getStatus().sending) closeCamera();
    },
    [],
  );

  async function handleOpenCamera() {
    setError(null);
    setOpeningCamera(true);
    try {
      setDeviceStream(await openCamera());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOpeningCamera(false);
    }
  }

  function chooseLiveSource(source: LiveSource) {
    setLiveSource(source);
    if (source === "device") {
      // One device camera per session: keep only the first camera's settings.
      setCameras((cams) => [{ ...cams[0], region: "full" }]);
    }
  }
  const nextKey = useRef(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const multi = cameras.length > 1 && !deviceCamera;

  function updateCamera(key: number, patch: Partial<CameraSetup>) {
    setCameras((cams) => cams.map((c) => (c.key === key ? { ...c, ...patch } : c)));
  }

  function addCamera() {
    setCameras((cams) => {
      // Typical setup: first camera on the left half, second on the right.
      const first = cams.length === 1 && cams[0].region === "full" ? [{ ...cams[0], region: "left" as const }] : cams;
      const used = new Set(first.map((c) => c.region));
      const region: CourtRegion = !used.has("right") ? "right" : !used.has("left") ? "left" : "full";
      return [...first, newCamera(nextKey.current++, region)];
    });
  }

  function removeCamera(key: number) {
    setCameras((cams) => {
      const rest = cams.filter((c) => c.key !== key);
      // Back to a single camera: it films the whole court again.
      return rest.length === 1 ? [{ ...rest[0], region: "full" }] : rest;
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const incomplete = cameras.findIndex((c) => c.calibrationIncomplete);
    if (incomplete !== -1) {
      setError(
        `Termina de marcar las 4 esquinas${multi ? ` de la cámara ${incomplete + 1}` : ""} o desactiva la calibración.`,
      );
      return;
    }
    setSubmitting(true);
    try {
      let matchId: number;
      if (mode === "upload") {
        const missing = cameras.findIndex((c) => !c.file);
        if (missing !== -1) throw new Error(multi ? `Selecciona el vídeo de la cámara ${missing + 1}` : "Selecciona un vídeo");
        matchId = multi
          ? (await uploadMultiCameraMatch({ name, courtType, cameras })).id
          : (await uploadMatchVideo({ name, courtType, file: cameras[0].file!, corners: cameras[0].corners })).id;
      } else if (deviceCamera) {
        if (!currentStream()) setDeviceStream(await openCamera());
        const match = await startBrowserLiveMatch({ name, courtType, corners: cameras[0].corners });
        startSending(match.id);
        matchId = match.id;
      } else {
        const missing = cameras.findIndex((c) => !c.streamUrl);
        if (missing !== -1) {
          throw new Error(multi ? `Indica la URL de la cámara ${missing + 1}` : "Indica la URL del stream (RTSP)");
        }
        matchId = multi
          ? (await startMultiCameraLive({ name, courtType, cameras })).id
          : (await startLiveMatch({ name, courtType, streamUrl: cameras[0].streamUrl, corners: cameras[0].corners }))
              .id;
      }
      navigate(`/sesiones/${matchId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="container" style={{ maxWidth: 760 }}>
      <div className="page-header">
        <div>
          <h1>Nueva sesión</h1>
          <p>Analiza un entrenamiento o partido con tus cámaras fijas.</p>
        </div>
      </div>

      <div className="tabs">
        <button type="button" className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}>
          Subir vídeo
        </button>
        <button type="button" className={mode === "live" ? "active" : ""} onClick={() => setMode("live")}>
          En directo
        </button>
      </div>

      <form onSubmit={handleSubmit} className="card">
        <div className="form-group">
          <label>Nombre de la sesión</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Entreno martes - ataque posicional"
            required
          />
        </div>

        <div className="form-group">
          <label>Modalidad</label>
          <select value={courtType} onChange={(e) => setCourtType(e.target.value as CourtType)}>
            <option value="piso">Pista (indoor)</option>
            <option value="playa">Balonmano playa</option>
          </select>
        </div>

        {mode === "live" && (
          <div className="form-group">
            <label>Cámara</label>
            <div className="source-choice">
              <button
                type="button"
                className={`btn btn-small${liveSource === "device" ? " active" : ""}`}
                onClick={() => chooseLiveSource("device")}
              >
                📱 Cámara de este dispositivo
              </button>
              <button
                type="button"
                className={`btn btn-small${liveSource === "rtsp" ? " active" : ""}`}
                onClick={() => chooseLiveSource("rtsp")}
              >
                📹 Cámara IP (RTSP)
              </button>
            </div>
          </div>
        )}

        {deviceCamera && (
          <fieldset className="camera-single">
            {!cameraSupported() && (
              <p className="error-text">
                {window.isSecureContext
                  ? "Este navegador no permite usar la cámara."
                  : "El navegador solo permite usar la cámara en páginas https:// (usa el enlace público de Colab)."}
              </p>
            )}
            {deviceStream ? (
              <DeviceCameraPreview stream={deviceStream} />
            ) : (
              <div className="form-group">
                <button type="button" className="btn" onClick={handleOpenCamera} disabled={openingCamera}>
                  {openingCamera ? "Abriendo cámara…" : "Activar cámara"}
                </button>
              </div>
            )}
            <p className="helper-text">
              Coloca el móvil fijo (trípode o apoyado), en horizontal, encuadrando la pista desde la
              banda. Mientras dure la sesión deja esta página abierta y en primer plano: la imagen se
              envía a la app para analizarla.
            </p>
            {deviceStream && (
              <CalibrationFields
                grabFrame={captureFrame}
                region="full"
                onChange={(corners, incomplete) => {
                  updateCamera(cameras[0].key, { corners, calibrationIncomplete: incomplete });
                  if (!incomplete) setError(null);
                }}
              />
            )}
          </fieldset>
        )}

        {!deviceCamera && cameras.map((cam, i) => (
          <fieldset key={cam.key} className={multi ? "camera-card" : "camera-single"}>
            {multi && (
              <legend>
                Cámara {i + 1}
                {i === 0 && <span className="helper-text"> · referencia de tiempo</span>}
              </legend>
            )}
            {multi && (
              <div className="camera-row">
                <div className="form-group">
                  <label>Nombre</label>
                  <input
                    type="text"
                    value={cam.name}
                    placeholder={`Cámara ${i + 1}`}
                    onChange={(e) => updateCamera(cam.key, { name: e.target.value })}
                  />
                </div>
                <div className="form-group">
                  <label>Zona que graba</label>
                  <select
                    value={cam.region}
                    onChange={(e) => updateCamera(cam.key, { region: e.target.value as CourtRegion })}
                  >
                    {(Object.keys(REGION_LABEL) as CourtRegion[]).map((r) => (
                      <option key={r} value={r}>
                        {REGION_LABEL[r]}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}

            {mode === "upload" ? (
              <div className="form-group">
                <label>Archivo de vídeo</label>
                <input
                  type="file"
                  accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska"
                  onChange={(e) => updateCamera(cam.key, { file: e.target.files?.[0] ?? null })}
                  required
                />
                {!multi && <p className="helper-text">Formatos soportados: MP4, MOV, AVI, MKV.</p>}
              </div>
            ) : (
              <div className="form-group">
                <label>URL del stream (RTSP)</label>
                <input
                  type="text"
                  value={cam.streamUrl}
                  onChange={(e) => updateCamera(cam.key, { streamUrl: e.target.value })}
                  placeholder="rtsp://usuario:contraseña@192.168.1.50:554/stream1"
                  required
                />
                {!multi && (
                  <p className="helper-text">
                    También acepta una URL HTTP/webcam compatible con OpenCV (ej. una cámara IP con
                    stream MJPEG).
                  </p>
                )}
              </div>
            )}

            {multi && i > 0 && mode === "upload" && (
              <div className="form-group">
                <label>
                  <input
                    type="checkbox"
                    checked={cam.autoSync}
                    onChange={(e) => updateCamera(cam.key, { autoSync: e.target.checked })}
                    style={{ marginRight: 8 }}
                  />
                  Sincronizar automáticamente con la cámara 1 por el sonido
                </label>
                {cam.autoSync ? (
                  <p className="helper-text">
                    Se usan los sonidos que oyen ambas cámaras (silbato, botes, lanzamientos). Si no se
                    puede, podrás ajustar el desfase a mano después del análisis.
                  </p>
                ) : (
                  <div className="offset-field">
                    <input
                      type="number"
                      step="0.1"
                      value={cam.offsetS}
                      onChange={(e) => updateCamera(cam.key, { offsetS: Number(e.target.value) })}
                    />
                    <span className="helper-text">
                      segundos que esta cámara empezó a grabar <strong>después</strong> de la cámara 1
                      (negativo si empezó antes).
                    </span>
                  </div>
                )}
              </div>
            )}

            <CalibrationFields
              videoFile={mode === "upload" ? cam.file : null}
              streamUrl={mode === "live" ? cam.streamUrl : undefined}
              region={cam.region}
              onChange={(corners, incomplete) => {
                updateCamera(cam.key, { corners, calibrationIncomplete: incomplete });
                if (!incomplete) setError(null);
              }}
            />

            {multi && (
              <button type="button" className="btn btn-small btn-danger" onClick={() => removeCamera(cam.key)}>
                Quitar cámara {i + 1}
              </button>
            )}
          </fieldset>
        ))}

        {!deviceCamera && cameras.length < MAX_CAMERAS && (
          <div className="form-group">
            <button type="button" className="btn" onClick={addCamera}>
              + Añadir {multi ? "otra cámara" : "una segunda cámara"}
            </button>
            {!multi && (
              <p className="helper-text">
                ¿Grabas con varias cámaras (por ejemplo, una por cada mitad de la pista)? Añádelas y
                la app unirá lo que ve cada una en una sola sesión.
              </p>
            )}
          </div>
        )}

        {error && <p className="error-text">{error}</p>}

        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting
            ? "Enviando…"
            : mode === "upload"
              ? multi
                ? `Analizar ${cameras.length} vídeos`
                : "Analizar vídeo"
              : deviceCamera
                ? "Empezar a analizar con esta cámara"
                : "Iniciar transmisión en directo"}
        </button>
      </form>
    </div>
  );
}
