import { useState } from "react";
import type { PlayerRole, PlayerStat, PlayerUpdate, TeamLabel, TeamNames, TeamSummary } from "../types";
import { heatmapUrl } from "../api/client";
import { TeamChip, teamName } from "./TeamSummary";

export const ROLE_LABEL: Record<PlayerRole, string> = {
  jugador: "Jugador",
  portero: "Portero",
  arbitro: "Árbitro / otro",
};

function sortedByTeam(players: PlayerStat[]): PlayerStat[] {
  // Team A, then team B, then people without a team; by distance within each.
  const rank = (p: PlayerStat) => (p.team === "A" ? 0 : p.team === "B" ? 1 : 2);
  return [...players].sort((a, b) => rank(a) - rank(b) || b.distance_m - a.distance_m);
}

function EditRow({
  player,
  teamNames,
  canEditTeams,
  onSave,
  onCancel,
}: {
  player: PlayerStat;
  teamNames: TeamNames | null;
  canEditTeams: boolean;
  onSave: (update: PlayerUpdate) => Promise<void>;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState(player.label);
  const [team, setTeam] = useState<TeamLabel | "">(player.team ?? "");
  const [role, setRole] = useState<PlayerRole>(player.role ?? "jugador");
  const [saving, setSaving] = useState(false);

  async function save(update: PlayerUpdate) {
    setSaving(true);
    try {
      await onSave(update);
    } catch {
      // The parent shows the error; stay in edit mode so nothing is lost.
    } finally {
      setSaving(false);
    }
  }

  function changes(): PlayerUpdate {
    const update: PlayerUpdate = {};
    if (label.trim() !== player.label) update.label = label;
    if (canEditTeams && (team || null) !== player.team) update.team = team || null;
    if (canEditTeams && role !== (player.role ?? "jugador")) update.role = role;
    return update;
  }

  return (
    <tr className="editing-row">
      <td>
        <input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} autoFocus />
      </td>
      <td>
        <select
          value={team}
          disabled={!canEditTeams || role === "arbitro"}
          onChange={(e) => setTeam(e.target.value as TeamLabel | "")}
        >
          <option value="A">{teamName("A", teamNames)}</option>
          <option value="B">{teamName("B", teamNames)}</option>
          <option value="">Sin equipo</option>
        </select>
      </td>
      <td>
        <select
          value={role}
          disabled={!canEditTeams}
          onChange={(e) => {
            const next = e.target.value as PlayerRole;
            setRole(next);
            if (next === "arbitro") setTeam("");
          }}
        >
          {(Object.keys(ROLE_LABEL) as PlayerRole[]).map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
      </td>
      <td colSpan={5}>
        <div className="row-actions">
          <button
            type="button"
            className="btn btn-small btn-primary"
            disabled={saving}
            onClick={() => {
              const update = changes();
              if (Object.keys(update).length === 0) onCancel();
              else save(update);
            }}
          >
            {saving ? "Guardando…" : "Guardar"}
          </button>
          <button type="button" className="btn btn-small" disabled={saving} onClick={onCancel}>
            Cancelar
          </button>
          {canEditTeams && (
            <button
              type="button"
              className="btn btn-small"
              disabled={saving}
              title="Quita tus correcciones de equipo/rol para este jugador y vuelve a la asignación automática"
              onClick={() => save({ reset: true })}
            >
              Automático
            </button>
          )}
        </div>
        {!canEditTeams && (
          <p className="helper-text">
            Esta sesión se analizó con una versión anterior: solo se puede cambiar el nombre.
          </p>
        )}
      </td>
    </tr>
  );
}

export default function PlayerStatsTable({
  matchId,
  players,
  teamSummary,
  teamNames = null,
  canEditTeams = false,
  onUpdate,
}: {
  matchId: number;
  players: PlayerStat[];
  teamSummary: TeamSummary | null;
  teamNames?: TeamNames | null;
  canEditTeams?: boolean;
  /** When given, each row gets an "Editar" button. */
  onUpdate?: (trackId: number, update: PlayerUpdate) => Promise<void>;
}) {
  const [editing, setEditing] = useState<number | null>(null);

  if (players.length === 0) {
    return <p className="helper-text">No se detectaron jugadores todavía.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Jugador</th>
          <th>Equipo</th>
          <th>Rol</th>
          <th>Distancia</th>
          <th>Vel. media</th>
          <th>Vel. máx</th>
          <th>Sprints</th>
          <th>Mapa de calor</th>
          {onUpdate && <th />}
        </tr>
      </thead>
      <tbody>
        {sortedByTeam(players).map((p) =>
          onUpdate && editing === p.track_id ? (
            <EditRow
              key={p.track_id}
              player={p}
              teamNames={teamNames}
              canEditTeams={canEditTeams}
              onCancel={() => setEditing(null)}
              onSave={async (update) => {
                await onUpdate(p.track_id, update);
                setEditing(null);
              }}
            />
          ) : (
            <tr key={p.track_id}>
              <td>{p.label}</td>
              <td>
                <TeamChip team={p.team} summary={teamSummary} names={teamNames} />
              </td>
              <td>{p.role ? ROLE_LABEL[p.role] : "—"}</td>
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
              {onUpdate && (
                <td>
                  <button type="button" className="link-button" onClick={() => setEditing(p.track_id)}>
                    Editar
                  </button>
                </td>
              )}
            </tr>
          ),
        )}
      </tbody>
    </table>
  );
}
