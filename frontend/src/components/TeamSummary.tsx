import { useState } from "react";
import type { TeamLabel, TeamNames, TeamSummary as TeamSummaryData } from "../types";

const TEAMS: TeamLabel[] = ["A", "B"];
const FALLBACK_COLOR = "#6b7894";

const ROWS: { key: "players" | "pases" | "perdidas" | "tiros" | "goles"; label: string }[] = [
  { key: "players", label: "Jugadores" },
  { key: "pases", label: "Pases" },
  { key: "perdidas", label: "Pérdidas" },
  { key: "tiros", label: "Tiros" },
  { key: "goles", label: "Goles" },
];

export function teamName(team: TeamLabel, names: TeamNames | null | undefined): string {
  return names?.[team] || `Equipo ${team}`;
}

export function TeamChip({
  team,
  summary,
  names,
}: {
  team: TeamLabel | null;
  summary: TeamSummaryData | null;
  names?: TeamNames | null;
}) {
  if (!team) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  const color = summary?.[team]?.color ?? FALLBACK_COLOR;
  return (
    <span className="team-chip">
      <span className="swatch" style={{ background: color }} />
      {teamName(team, names)}
    </span>
  );
}

function EditableTeamName({
  team,
  names,
  onRename,
}: {
  team: TeamLabel;
  names: TeamNames | null;
  onRename: (team: TeamLabel, name: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(names?.[team] ?? "");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await onRename(team, value);
      setEditing(false);
    } catch {
      // The parent shows the error; keep the input open.
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <button
        type="button"
        className="link-button"
        title="Cambiar nombre"
        onClick={() => {
          setValue(names?.[team] ?? "");
          setEditing(true);
        }}
      >
        ✎
      </button>
    );
  }
  return (
    <form
      className="inline-edit"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <input
        autoFocus
        value={value}
        maxLength={60}
        placeholder={`Equipo ${team}`}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
      />
      <button type="submit" className="btn btn-small btn-primary" disabled={saving}>
        OK
      </button>
    </form>
  );
}

export default function TeamSummary({
  summary,
  names = null,
  onRename,
}: {
  summary: TeamSummaryData | null;
  names?: TeamNames | null;
  /** When given, team names can be edited inline. */
  onRename?: (team: TeamLabel, name: string) => Promise<void>;
}) {
  const teams = TEAMS.filter((t) => summary?.[t]);
  if (!summary || teams.length === 0) {
    return (
      <p className="helper-text">
        No se pudieron separar los equipos (hacen falta jugadores de dos equipos con camisetas de
        colores distintos y visibles durante varios frames). Puedes asignarlos a mano desde la
        tabla de jugadores.
      </p>
    );
  }

  const hasPossession = teams.some((t) => (summary[t]?.possession_s ?? 0) > 0);

  return (
    <>
      {hasPossession && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            {teams.map((t) => (
              <span key={t} className="team-chip">
                <span className="swatch" style={{ background: summary[t]!.color ?? FALLBACK_COLOR }} />
                {teamName(t, names)} · {summary[t]!.possession_pct.toFixed(0)}% posesión
              </span>
            ))}
          </div>
          <div className="possession-bar" aria-label="Reparto de la posesión">
            {teams.map((t) => (
              <div
                key={t}
                style={{
                  width: `${summary[t]!.possession_pct}%`,
                  background: summary[t]!.color ?? FALLBACK_COLOR,
                }}
              />
            ))}
          </div>
        </>
      )}
      <table className="team-summary-table">
        <thead>
          <tr>
            <th />
            {teams.map((t) => (
              <th key={t}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <TeamChip team={t} summary={summary} names={names} />
                  {onRename && <EditableTeamName team={t} names={names} onRename={onRename} />}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              {teams.map((t) => (
                <td key={t}>{summary[t]![row.key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="helper-text">
        Equipos asignados automáticamente por el color de la camiseta (los porteros, por su
        posición y sus pases). Los árbitros quedan sin equipo y no cuentan para la posesión.
      </p>
    </>
  );
}
