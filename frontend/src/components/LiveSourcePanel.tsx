import { useEffect, useState } from "react";
import {
  currentStream,
  openCamera,
  startSending,
  subscribe,
  type SenderStatus,
} from "../camera/browserCamera";
import type { LiveStatsSnapshot, MatchDetail } from "../types";
import DeviceCameraPreview from "./DeviceCameraPreview";

/** Where the live image comes from, and whether it is arriving. */
export default function LiveSourcePanel({ match, snapshot }: { match: MatchDetail; snapshot: LiveStatsSnapshot }) {
  const [sender, setSender] = useState<SenderStatus | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);
  useEffect(() => subscribe(setSender), []);

  const noImageYet = snapshot.frames_processed === 0 && snapshot.elapsed_s > 10;
  const preparing = !snapshot.ai_ready && (
    <p className="helper-text">⏳ Preparando la IA… (los primeros segundos de cada sesión)</p>
  );

  if (match.live_source === "rtsp") {
    if (!snapshot.source_connected) {
      return <p className="error-text">No llega imagen de la cámara IP: {snapshot.source_error ?? "conexión perdida"}.</p>;
    }
    return preparing || (noImageYet ? <p className="helper-text">Conectando con la cámara IP…</p> : null);
  }

  const sendingHere = sender?.sending && sender.matchId === match.id;
  const stream = currentStream();

  async function resume() {
    if (!confirm("¿Enviar la imagen de la cámara de ESTE dispositivo a la sesión? Hazlo solo en el móvil que está grabando.")) {
      return;
    }
    setResumeError(null);
    try {
      await openCamera();
      startSending(match.id);
    } catch (e) {
      setResumeError(e instanceof Error ? e.message : String(e));
    }
  }

  if (sendingHere && stream) {
    return (
      <div>
        <DeviceCameraPreview stream={stream} />
        {preparing || (
          <div className="live-sender-status helper-text">
            <span className="pulse-dot" />
            Enviando imagen · {sender!.framesProcessed} fotogramas analizados
          </div>
        )}
        {sender!.error && <p className="error-text">Problema al enviar: {sender!.error} (reintentando…)</p>}
        <p className="helper-text">
          Deja esta página abierta y el móvil desbloqueado mientras dure el entrenamiento.
        </p>
      </div>
    );
  }

  return (
    <div>
      <p className="helper-text">
        La imagen llega de la cámara del dispositivo que inició la sesión
        {snapshot.frames_processed > 0 ? ` (${snapshot.frames_processed} fotogramas analizados).` : "."}
        {noImageYet && " Todavía no ha llegado ninguna imagen."}
      </p>
      {sender?.error && sender.matchId === match.id && <p className="error-text">{sender.error}</p>}
      <p className="helper-text">
        ¿Eres el móvil que graba y se cortó la cámara (por ejemplo, al recargar la página)?{" "}
        <button type="button" className="link-button" onClick={resume}>
          Reanudar con la cámara de este dispositivo
        </button>
      </p>
      {resumeError && <p className="error-text">{resumeError}</p>}
    </div>
  );
}
