import { useState } from "react";
import { replayVideoUrl } from "../api/client";
import type { MatchDetail } from "../types";

/** Generate / watch / download the recreation as an MP4 (to share it). */
export default function ReplayVideoExport({
  match,
  onRender,
}: {
  match: MatchDetail;
  onRender: (speed: 1 | 2 | 4) => Promise<void>;
}) {
  const [speed, setSpeed] = useState<1 | 2 | 4>((match.duration_s ?? 0) > 15 * 60 ? 4 : 1);
  const [busy, setBusy] = useState(false);
  const job = match.active_job;
  const rendering = job?.kind === "replay_video";
  const video = match.replay_video;
  const url = video ? replayVideoUrl(match.id, video.created_at) : null;
  const estimateMin = Math.max(1, Math.round(((match.duration_s ?? 0) / speed / 3) / 60));

  async function start() {
    setBusy(true);
    try {
      await onRender(speed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {url && !rendering && (
        <>
          <video className="replay-video" src={url} controls playsInline preload="metadata" />
          <div className="status-actions">
            <a className="btn btn-small btn-primary" href={url} download>
              Descargar vídeo (MP4)
            </a>
            <span className="helper-text">Velocidad x{video!.speed}. Se puede compartir por WhatsApp.</span>
          </div>
        </>
      )}

      {rendering ? (
        <>
          <p className="helper-text">
            {job!.status === "queued"
              ? "En cola para generar el vídeo…"
              : `Generando el vídeo… ${Math.round(job!.progress * 100)}%`}
          </p>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${Math.round(job!.progress * 100)}%` }} />
          </div>
        </>
      ) : (
        <div className="status-actions">
          <label className="replay-option">
            Velocidad del vídeo
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value) as 1 | 2 | 4)}>
              <option value={1}>x1 (tiempo real)</option>
              <option value={2}>x2</option>
              <option value={4}>x4</option>
            </select>
          </label>
          <button type="button" className="btn btn-small" onClick={start} disabled={busy || !!job}>
            {url ? "Volver a generar" : "Generar vídeo MP4"}
          </button>
          <span className="helper-text">Tardará unos {estimateMin} min.</span>
        </div>
      )}
    </div>
  );
}
