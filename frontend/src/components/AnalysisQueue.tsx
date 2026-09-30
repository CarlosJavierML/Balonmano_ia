import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { cancelAnalysis, listJobs } from "../api/client";
import type { QueueItem } from "../types";

const POLL_MS = 3000;

/**
 * Live view of the analysis queue: the job(s) running now with their
 * progress, then the ones waiting in the order they will run.
 * Calls `onChange` whenever the set of active jobs changes, so the parent
 * can refresh anything that depends on it (e.g. session statuses).
 */
export default function AnalysisQueue({ onChange }: { onChange?: () => void }) {
  const [jobs, setJobs] = useState<QueueItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let lastKey = "";

    async function tick() {
      try {
        const next = await listJobs();
        if (!active) return;
        setJobs(next);
        setError(null);
        const key = next.map((j) => `${j.id}:${j.status}`).join(",");
        if (lastKey && key !== lastKey) onChange?.();
        lastKey = key;
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : String(e));
      }
    }

    tick();
    const interval = window.setInterval(tick, POLL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [onChange]);

  async function handleCancel(item: QueueItem) {
    const verb = item.status === "running" ? "Detener el análisis en curso" : "Quitar de la cola";
    if (!confirm(`${verb} de «${item.match_name}»?`)) return;
    try {
      await cancelAnalysis(item.match_id);
      setJobs(await listJobs());
      onChange?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (jobs.length === 0 && !error) return null;

  const queued = jobs.filter((j) => j.status === "queued").length;

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <h2>Cola de análisis</h2>
      <p className="helper-text" style={{ marginTop: -6, marginBottom: 12 }}>
        Los vídeos se analizan de uno en uno para no saturar el ordenador
        {queued > 0 ? ` · ${queued} en espera` : ""}.
      </p>
      {error && <p className="error-text">{error}</p>}
      <ul className="queue-list">
        {jobs.map((j) => {
          const running = j.status === "running";
          const pct = Math.round(j.progress * 100);
          return (
            <li key={j.id} className="queue-item">
              <span className={`position${running ? " running" : ""}`} title={running ? "Analizando" : "Posición en la cola"}>
                {running ? "▶" : `#${j.position}`}
              </span>
              <div>
                <Link to={`/sesiones/${j.match_id}`}>
                  <strong>{j.match_name}</strong>
                </Link>
                <div className="helper-text" style={{ marginTop: 2 }}>
                  {running
                    ? j.cancel_requested
                      ? "Cancelando…"
                      : `${
                          j.kind === "tracking"
                            ? "Procesando directo"
                            : j.kind === "replay_video"
                              ? "Generando vídeo de la recreación"
                              : "Analizando vídeo"
                        } · ${pct}%`
                    : j.position === 1
                      ? "Siguiente en la cola"
                      : "En espera"}
                  {j.attempts > 1 && ` · intento ${j.attempts} de ${j.max_attempts}`}
                </div>
                {running && (
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${pct}%` }} />
                  </div>
                )}
              </div>
              <button
                type="button"
                className="btn btn-small btn-danger"
                disabled={j.cancel_requested}
                onClick={() => handleCancel(j)}
              >
                {running ? "Detener" : "Quitar"}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
