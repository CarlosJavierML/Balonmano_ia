import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listMatches } from "../api/client";
import { CourtBadge, StatusBadge } from "../components/Badges";
import type { Match } from "../types";

export default function Dashboard() {
  const [matches, setMatches] = useState<Match[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listMatches()
      .then(setMatches)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="container">
      <div className="page-header">
        <div>
          <h1>Sesiones de entrenamiento</h1>
          <p>Estadísticas y eventos tácticos generados por IA a partir de tu cámara fija.</p>
        </div>
        <Link to="/nueva" className="btn btn-primary">
          + Nueva sesión
        </Link>
      </div>

      {error && <p className="error-text">{error}</p>}

      {matches === null && !error && <p className="helper-text">Cargando…</p>}

      {matches && matches.length === 0 && (
        <div className="card empty-state">
          <p>Todavía no hay sesiones analizadas.</p>
          <Link to="/nueva" className="btn btn-primary">
            Analizar mi primer vídeo
          </Link>
        </div>
      )}

      {matches && matches.length > 0 && (
        <div className="match-list">
          {matches.map((m) => (
            <Link to={`/sesiones/${m.id}`} key={m.id} className="match-row">
              <div>
                <strong>{m.name}</strong>
                <div className="meta">
                  {new Date(m.created_at).toLocaleString("es-ES")} ·{" "}
                  {m.source_mode === "live" ? "En directo" : "Vídeo subido"}
                </div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <CourtBadge courtType={m.court_type} />
                <StatusBadge status={m.status} />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
