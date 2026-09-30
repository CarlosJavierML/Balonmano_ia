import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  cancelAnalysis,
  deleteMatch,
  getLiveSnapshot,
  getMatch,
  reanalyzeMatch,
  renameTeams,
  renderReplayVideo,
  reportUrl,
  stopLiveMatch,
  updateCamera,
  updatePlayer,
} from "../api/client";
import { CourtBadge, StatusBadge } from "../components/Badges";
import EventsTimeline from "../components/EventsTimeline";
import PlayerStatsTable from "../components/PlayerStatsTable";
import { DistanceChart, SpeedChart } from "../components/StatsCharts";
import { closeCamera, getStatus, stopSending } from "../camera/browserCamera";
import CamerasPanel from "../components/CamerasPanel";
import LiveSourcePanel from "../components/LiveSourcePanel";
import ReplayVideoExport from "../components/ReplayVideoExport";
import ReplayViewer from "../components/ReplayViewer";
import TeamSummary from "../components/TeamSummary";
import type { LiveStatsSnapshot, MatchDetail, MatchStatus, PlayerUpdate, TeamLabel } from "../types";

const POLL_MS = 3000;
const FINISHED: MatchStatus[] = ["done", "failed", "cancelled"];

function formatDuration(seconds: number | null): string {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

export default function SessionDetail() {
  const { id } = useParams();
  const matchId = Number(id);
  const navigate = useNavigate();

  const [match, setMatch] = useState<MatchDetail | null>(null);
  const [liveSnapshot, setLiveSnapshot] = useState<LiveStatsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  // Errors from edits (rename/corrections) shouldn't replace the whole page.
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Bumped to restart polling after queuing a new analysis.
  const [pollKey, setPollKey] = useState(0);
  const intervalRef = useRef<number | null>(null);

  useEffect(() => {
    let active = true;

    async function tick() {
      try {
        const m = await getMatch(matchId);
        if (!active) return;
        setMatch(m);

        if (m.source_mode === "live" && m.status === "processing") {
          try {
            const snap = await getLiveSnapshot(matchId);
            if (active) setLiveSnapshot(snap);
          } catch {
            if (active) setLiveSnapshot(null);
          }
        } else {
          setLiveSnapshot(null);
        }

        // Keep polling while a job (e.g. rendering the recreation video) runs.
        if (FINISHED.includes(m.status) && !m.active_job && intervalRef.current) {
          window.clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      } catch (e) {
        if (active) setError(String(e));
      }
    }

    tick();
    intervalRef.current = window.setInterval(tick, POLL_MS);
    return () => {
      active = false;
      if (intervalRef.current) window.clearInterval(intervalRef.current);
    };
  }, [matchId, pollKey]);

  async function handleStop() {
    setStopping(true);
    try {
      // This device was sending its camera: stop that first, then the session.
      if (getStatus().matchId === matchId) {
        stopSending();
        closeCamera();
      }
      await stopLiveMatch(matchId);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setStopping(false);
    }
  }

  async function handleRenameTeam(team: TeamLabel, name: string) {
    try {
      setMatch(await renameTeams(matchId, { [team]: name }));
      setActionError(null);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      throw e;
    }
  }

  async function handleUpdatePlayer(trackId: number, update: PlayerUpdate) {
    try {
      setMatch(await updatePlayer(matchId, trackId, update));
      setActionError(null);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      throw e;
    }
  }

  async function handleUpdateCamera(index: number, update: { time_offset_s?: number; auto_sync?: boolean }) {
    try {
      setMatch(await updateCamera(matchId, index, update));
      setActionError(null);
      setPollKey((k) => k + 1); // a re-fusion was queued: follow it
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      throw e;
    }
  }

  async function handleRenderVideo(speed: 1 | 2 | 4) {
    try {
      await renderReplayVideo(matchId, speed);
      setMatch(await getMatch(matchId));
      setActionError(null);
      setPollKey((k) => k + 1);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleCancel() {
    if (!confirm("¿Cancelar el análisis de esta sesión?")) return;
    setBusy(true);
    try {
      await cancelAnalysis(matchId);
      setMatch(await getMatch(matchId));
      setActionError(null);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleReanalyze() {
    const warning =
      match?.status === "done"
        ? "Se volverá a analizar el vídeo desde cero y se sustituirán las estadísticas actuales " +
          "(incluidos los nombres y correcciones de jugadores). ¿Continuar?"
        : "¿Volver a poner esta sesión en la cola de análisis?";
    if (!confirm(warning)) return;
    setBusy(true);
    try {
      await reanalyzeMatch(matchId);
      setMatch(await getMatch(matchId));
      setActionError(null);
      setPollKey((k) => k + 1);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!confirm("¿Eliminar esta sesión y todas sus estadísticas?")) return;
    try {
      await deleteMatch(matchId);
      navigate("/");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) {
    return (
      <div className="container">
        <p className="error-text">{error}</p>
      </div>
    );
  }

  if (!match) {
    return (
      <div className="container">
        <p className="helper-text">Cargando…</p>
      </div>
    );
  }

  const isLiveRunning = match.source_mode === "live" && match.status === "processing" && liveSnapshot !== null;
  const job = match.active_job;
  const canReanalyze = FINISHED.includes(match.status) && !job;

  return (
    <div className="container">
      <div className="page-header">
        <div>
          <h1>{match.name}</h1>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <CourtBadge courtType={match.court_type} />
            <StatusBadge status={match.status} />
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {isLiveRunning && (
            <button className="btn btn-danger" onClick={handleStop} disabled={stopping}>
              {stopping ? "Deteniendo…" : "Detener transmisión"}
            </button>
          )}
          {match.status === "done" && (
            <a className="btn btn-primary" href={reportUrl(match.id)} target="_blank" rel="noreferrer">
              Descargar informe PDF
            </a>
          )}
          {match.status === "done" && (
            <button className="btn" onClick={handleReanalyze} disabled={busy}>
              Reanalizar
            </button>
          )}
          <button className="btn btn-danger" onClick={handleDelete}>
            Eliminar
          </button>
        </div>
      </div>

      {isLiveRunning && liveSnapshot && (
        <div className="card">
          <h2>
            <span className="pulse-dot" />
            En directo
          </h2>
          <LiveSourcePanel match={match} snapshot={liveSnapshot} />
          <div className="grid grid-3">
            <div className="stat-tile">
              <div className="value">{formatDuration(liveSnapshot.elapsed_s)}</div>
              <div className="label">Tiempo transcurrido</div>
            </div>
            <div className="stat-tile">
              <div className="value">{liveSnapshot.active_tracks}</div>
              <div className="label">Jugadores activos</div>
            </div>
            <div className="stat-tile">
              <div className="value">{liveSnapshot.recent_events.length}</div>
              <div className="label">Eventos recientes</div>
            </div>
          </div>
          <div className="grid grid-2" style={{ marginTop: 18 }}>
            <div>
              <h3>Distancia recorrida</h3>
              <DistanceChart players={liveSnapshot.player_stats} />
            </div>
            <div>
              <h3>Eventos</h3>
              <EventsTimeline events={liveSnapshot.recent_events} players={liveSnapshot.player_stats} />
            </div>
          </div>
          {liveSnapshot.team_summary && (
            <div style={{ marginTop: 18 }}>
              <h3>Equipos</h3>
              <TeamSummary summary={liveSnapshot.team_summary} />
            </div>
          )}
        </div>
      )}

      {(match.status === "pending" || match.status === "processing") && !isLiveRunning && (
        <div className="card">
          {match.status === "pending" ? (
            <>
              <h2>En cola</h2>
              <p className="helper-text">
                {job?.position === 1 || job?.position == null
                  ? "Es la siguiente en analizarse: empezará en cuanto termine el análisis en curso."
                  : `Posición ${job.position} en la cola de análisis. Los vídeos se analizan de uno en uno.`}
              </p>
              {match.error_message && <p className="helper-text">{match.error_message}</p>}
            </>
          ) : (
            <>
              <h2>Analizando…</h2>
              <p className="helper-text">
                {match.source_mode === "live"
                  ? "Calculando las estadísticas finales de la transmisión…"
                  : "Detección, tracking y eventos con el motor de visión. Puede tardar varios minutos según la duración del vídeo."}
                {job && job.attempts > 1 && ` (intento ${job.attempts} de ${job.max_attempts})`}
              </p>
              {match.source_mode === "upload" && (
                <>
                  <div
                    className="progress-track"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(match.progress * 100)}
                  >
                    <div className="progress-fill" style={{ width: `${Math.round(match.progress * 100)}%` }} />
                  </div>
                  <p className="helper-text">{Math.round(match.progress * 100)}% completado</p>
                </>
              )}
            </>
          )}
          {job && (
            <div className="status-actions">
              <button className="btn btn-small btn-danger" onClick={handleCancel} disabled={busy || job.cancel_requested}>
                {job.cancel_requested ? "Cancelando…" : "Cancelar análisis"}
              </button>
            </div>
          )}
        </div>
      )}

      {(match.status === "failed" || match.status === "cancelled") && (
        <div className="card">
          {match.status === "failed" ? (
            <p className="error-text">El análisis falló: {match.error_message}</p>
          ) : (
            <p className="helper-text">El análisis se canceló.</p>
          )}
          {canReanalyze && (
            <div className="status-actions">
              <button className="btn btn-primary btn-small" onClick={handleReanalyze} disabled={busy}>
                {match.status === "failed" ? "Reintentar análisis" : "Volver a analizar"}
              </button>
            </div>
          )}
        </div>
      )}

      {actionError && (
        <div className="card">
          <p className="error-text">{actionError}</p>
        </div>
      )}

      {match.cameras.length > 0 && (
        <div className="card">
          <h2>Cámaras</h2>
          <CamerasPanel
            cameras={match.cameras}
            report={match.fusion_report}
            canEdit={FINISHED.includes(match.status) && !match.active_job && match.editable}
            onUpdate={handleUpdateCamera}
          />
        </div>
      )}

      {match.status === "done" && (
        <>
          <div className="card">
            <div className="grid grid-3">
              <div className="stat-tile">
                <div className="value">{formatDuration(match.duration_s)}</div>
                <div className="label">Duración analizada</div>
              </div>
              <div className="stat-tile">
                <div className="value">{match.player_stats.length}</div>
                <div className="label">Jugadores detectados</div>
              </div>
              <div className="stat-tile">
                <div className="value">{match.events.length}</div>
                <div className="label">Eventos tácticos</div>
              </div>
            </div>
          </div>

          {match.editable && (
            <div className="card">
              <h2>Recreación de la sesión</h2>
              <ReplayViewer
                matchId={match.id}
                version={`${match.events.length}-${match.player_stats.map((p) => `${p.label}${p.team}`).join()}-${match.team_names ? JSON.stringify(match.team_names) : ""}`}
              />
              <h3 style={{ marginTop: 18 }}>Vídeo para compartir</h3>
              <ReplayVideoExport match={match} onRender={handleRenderVideo} />
            </div>
          )}

          <div className="card">
            <h2>Equipos</h2>
            <TeamSummary summary={match.team_summary} names={match.team_names} onRename={handleRenameTeam} />
          </div>

          <div className="card">
            <h2>Distancia recorrida</h2>
            <DistanceChart players={match.player_stats} />
          </div>

          <div className="card">
            <h2>Velocidad</h2>
            <SpeedChart players={match.player_stats} />
          </div>

          <div className="card">
            <h2>Estadísticas por jugador</h2>
            <p className="helper-text" style={{ marginTop: -6 }}>
              Pulsa «Editar» para poner nombre a un jugador o corregir su equipo o rol; los pases,
              pérdidas y la posesión se recalculan al momento.
            </p>
            <PlayerStatsTable
              matchId={match.id}
              players={match.player_stats}
              teamSummary={match.team_summary}
              teamNames={match.team_names}
              canEditTeams={match.editable}
              onUpdate={handleUpdatePlayer}
            />
          </div>

          <div className="card">
            <h2>Eventos tácticos</h2>
            <EventsTimeline events={match.events} players={match.player_stats} teamNames={match.team_names} />
          </div>
        </>
      )}
    </div>
  );
}
