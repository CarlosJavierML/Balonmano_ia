import type { TeamLabel, TeamSummary as TeamSummaryData } from "../types";

const TEAMS: TeamLabel[] = ["A", "B"];

const ROWS: { key: "players" | "pases" | "perdidas" | "tiros" | "goles"; label: string }[] = [
  { key: "players", label: "Jugadores" },
  { key: "pases", label: "Pases" },
  { key: "perdidas", label: "Pérdidas" },
  { key: "tiros", label: "Tiros" },
  { key: "goles", label: "Goles" },
];

export function TeamChip({ team, summary }: { team: TeamLabel | null; summary: TeamSummaryData | null }) {
  if (!team) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  const color = summary?.[team]?.color;
  return (
    <span className="team-chip">
      {color && <span className="swatch" style={{ background: color }} />}
      {team}
    </span>
  );
}

export default function TeamSummary({ summary }: { summary: TeamSummaryData | null }) {
  const teams = TEAMS.filter((t) => summary?.[t]);
  if (!summary || teams.length === 0) {
    return (
      <p className="helper-text">
        No se pudieron separar los equipos (hacen falta jugadores de dos equipos con camisetas de
        colores distintos y visibles durante varios frames).
      </p>
    );
  }

  const hasPossession = teams.some((t) => (summary[t]?.possession_s ?? 0) > 0);

  return (
    <>
      {hasPossession && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            {teams.map((t) => (
              <span key={t} className="team-chip">
                <span className="swatch" style={{ background: summary[t]!.color }} />
                Equipo {t} · {summary[t]!.possession_pct.toFixed(0)}% posesión
              </span>
            ))}
          </div>
          <div className="possession-bar" aria-label="Reparto de la posesión">
            {teams.map((t) => (
              <div key={t} style={{ width: `${summary[t]!.possession_pct}%`, background: summary[t]!.color }} />
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
                <TeamChip team={t} summary={summary} />
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
        Equipos asignados automáticamente por el color de la camiseta. Los árbitros y personas con
        colores distintos quedan sin equipo.
      </p>
    </>
  );
}
