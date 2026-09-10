import type { PlayerStat } from "../types";
import { heatmapUrl } from "../api/client";

export default function PlayerStatsTable({
  matchId,
  players,
}: {
  matchId: number;
  players: PlayerStat[];
}) {
  if (players.length === 0) {
    return <p className="helper-text">No se detectaron jugadores todavía.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Jugador</th>
          <th>Distancia</th>
          <th>Vel. media</th>
          <th>Vel. máx</th>
          <th>Sprints</th>
          <th>Mapa de calor</th>
        </tr>
      </thead>
      <tbody>
        {players.map((p) => (
          <tr key={p.track_id}>
            <td>{p.label}</td>
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
