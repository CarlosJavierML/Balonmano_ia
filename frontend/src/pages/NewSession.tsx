import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { startLiveMatch, uploadMatchVideo } from "../api/client";
import CalibrationFields from "../components/CalibrationFields";
import type { CourtType, PixelCorner } from "../types";

type Mode = "upload" | "live";

export default function NewSession() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("upload");
  const [name, setName] = useState("");
  const [courtType, setCourtType] = useState<CourtType>("piso");
  const [file, setFile] = useState<File | null>(null);
  const [streamUrl, setStreamUrl] = useState("");
  const [corners, setCorners] = useState<PixelCorner[] | undefined>(undefined);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "upload") {
        if (!file) throw new Error("Selecciona un vídeo");
        const match = await uploadMatchVideo({ name, courtType, file, corners });
        navigate(`/sesiones/${match.id}`);
      } else {
        if (!streamUrl) throw new Error("Indica la URL del stream (RTSP)");
        const match = await startLiveMatch({ name, courtType, streamUrl, corners });
        navigate(`/sesiones/${match.id}`);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="container" style={{ maxWidth: 640 }}>
      <div className="page-header">
        <div>
          <h1>Nueva sesión</h1>
          <p>Analiza un entrenamiento o partido con tu cámara fija.</p>
        </div>
      </div>

      <div className="tabs">
        <button type="button" className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}>
          Subir vídeo
        </button>
        <button type="button" className={mode === "live" ? "active" : ""} onClick={() => setMode("live")}>
          En directo (RTSP)
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

        {mode === "upload" ? (
          <div className="form-group">
            <label>Archivo de vídeo</label>
            <input
              type="file"
              accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              required
            />
            <p className="helper-text">Formatos soportados: MP4, MOV, AVI, MKV.</p>
          </div>
        ) : (
          <div className="form-group">
            <label>URL del stream (RTSP)</label>
            <input
              type="text"
              value={streamUrl}
              onChange={(e) => setStreamUrl(e.target.value)}
              placeholder="rtsp://usuario:contraseña@192.168.1.50:554/stream1"
              required
            />
            <p className="helper-text">
              También acepta una URL HTTP/webcam compatible con OpenCV (ej. una cámara IP con
              stream MJPEG).
            </p>
          </div>
        )}

        <CalibrationFields onChange={setCorners} />

        {error && <p className="error-text">{error}</p>}

        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting
            ? "Enviando…"
            : mode === "upload"
              ? "Analizar vídeo"
              : "Iniciar transmisión en directo"}
        </button>
      </form>
    </div>
  );
}
