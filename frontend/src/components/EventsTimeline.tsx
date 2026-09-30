import type { MatchEvent, PlayerStat, TeamLabel, TeamNames } from "../types";
import { teamName } from "./TeamSummary";

interface Names {
  players: Map<number, string>;
  teams: TeamNames | null;
}

const EVENT_LABEL: Record<string, string> = {
  pase: "Pase",
  perdida: "Pérdida de balón",
  cambio_posesion: "Cambio de posesión",
  tiro: "Tiro",
  gol: "¡Gol!",
};

function formatTs(seconds: number): string {
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const s = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

function describe(event: MatchEvent, names: Names): string {
  const player = (id: number | null) => (id === null ? "?" : (names.players.get(id) ?? `#${id}`));
  const team = (t: unknown) => (t === "A" || t === "B" ? teamName(t as TeamLabel, names.teams) : undefined);
  if (
    event.event_type === "cambio_posesion" ||
    event.event_type === "pase" ||
    event.event_type === "perdida"
  ) {
    const teamFrom = team(event.meta?.team_from);
    const teamTo = team(event.meta?.team_to);
    const from = `${player(event.track_id_from)}${teamFrom ? ` (${teamFrom})` : ""}`;
    const to = `${player(event.track_id_to)}${teamTo ? ` (${teamTo})` : ""}`;
    return `${from} → ${to}`;
  }
  if (event.event_type === "tiro" || event.event_type === "gol") {
    const side = event.meta?.side as string | undefined;
    const speed = event.meta?.speed_kmh as number | undefined;
    const shooterTeam = team(event.meta?.team);
    const parts = [];
    if (event.track_id_from !== null) parts.push(player(event.track_id_from));
    if (shooterTeam) parts.push(shooterTeam);
    if (side) parts.push(`portería ${side === "left" ? "izquierda" : "derecha"}`);
    if (speed) parts.push(`${speed} km/h`);
    return parts.join(" · ");
  }
  return "";
}

export default function EventsTimeline({
  events,
  players = [],
  teamNames = null,
}: {
  events: MatchEvent[];
  /** Used to show custom player names instead of track ids. */
  players?: PlayerStat[];
  teamNames?: TeamNames | null;
}) {
  const names: Names = { players: new Map(players.map((p) => [p.track_id, p.label])), teams: teamNames };
  if (events.length === 0) {
    return <p className="helper-text">Todavía no se han detectado eventos.</p>;
  }
  const sorted = [...events].sort((a, b) => b.timestamp_s - a.timestamp_s);
  return (
    <ul className="timeline">
      {sorted.map((e, i) => (
        <li key={i} className={`event-${e.event_type}`}>
          <span className="time">{formatTs(e.timestamp_s)}</span>
          <strong>{EVENT_LABEL[e.event_type] ?? e.event_type}</strong>
          {describe(e, names) && (
            <span style={{ color: "var(--text-muted)" }}> — {describe(e, names)}</span>
          )}
        </li>
      ))}
    </ul>
  );
}
