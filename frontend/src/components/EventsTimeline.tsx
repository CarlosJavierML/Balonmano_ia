import type { MatchEvent } from "../types";

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

function describe(event: MatchEvent): string {
  if (
    event.event_type === "cambio_posesion" ||
    event.event_type === "pase" ||
    event.event_type === "perdida"
  ) {
    const teamFrom = event.meta?.team_from as string | undefined;
    const teamTo = event.meta?.team_to as string | undefined;
    const from = `#${event.track_id_from}${teamFrom ? ` (${teamFrom})` : ""}`;
    const to = `#${event.track_id_to}${teamTo ? ` (${teamTo})` : ""}`;
    return `${from} → ${to}`;
  }
  if (event.event_type === "tiro" || event.event_type === "gol") {
    const side = event.meta?.side as string | undefined;
    const speed = event.meta?.speed_kmh as number | undefined;
    const team = event.meta?.team as string | undefined;
    const parts = [];
    if (team) parts.push(`equipo ${team}`);
    if (side) parts.push(`portería ${side === "left" ? "izquierda" : "derecha"}`);
    if (speed) parts.push(`${speed} km/h`);
    return parts.join(" · ");
  }
  return "";
}

export default function EventsTimeline({ events }: { events: MatchEvent[] }) {
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
          {describe(e) && <span style={{ color: "var(--text-muted)" }}> — {describe(e)}</span>}
        </li>
      ))}
    </ul>
  );
}
