import type { PlayerStat, TeamSummary } from "../types";
import { heatmapUrl } from "../api/client";
import { TeamChip } from "./TeamSummary";

function sortedByTeam(players: PlayerStat[]): PlayerStat[] {
  // Team A, then team B, then players without a team; by distance within each.
  const rank = (p: PlayerStat) => (p.team === "A" ? 0 : p.team === "B" ? 1 : 2);
  return [...players].sort((a, b) => rank(a) - rank(b) || b.distance_m - a.distance_m);
}

export default function PlayerStatsTable({
  matchId,
  players,
  teamSummary,
}: {
  matchId: number;
  players: PlayerStat[];
  teamSummary: TeamSummary | null;
}) {
  if (players.length === 0) {
    return <p className="helper-text">No se detectaron jugadores todavía.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Jugador</th>
          <th>Equipo</th>
          <th>Distancia</th>
          <th>Vel. media</th>
          <th>Vel. máx</th>
          <th>Sprints</th>
          <th>Mapa de calor</th>
        </tr>
      </thead>
      <tbody>
        {sortedByTeam(players).map((p) => (
          <tr key={p.track_id}>
            <td>{p.label}</td>
            <td>
              <TeamChip team={p.team} summary={teamSummary} />
            </td>
            <td>{p.distance_m.toFixed(0)} m</td>
            <td>{p.avg_speed_kmh.toFixed(1)} km/h</td>
            <td>{p.max_speed_kmh.toFixed(1)} km/h</td>
            <td>{p.sprint_count}</td>
            <td>
              {p.heatmap_path ? (
                <a href={heatmapUrl(matchId, p.track_id)} target="_blank" rel="noreferrer">
                  ver
                </a>
              ) : (
                "—"
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
