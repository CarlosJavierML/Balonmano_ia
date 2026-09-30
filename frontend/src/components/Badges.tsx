import type { CourtType, MatchStatus } from "../types";

const STATUS_LABEL: Record<MatchStatus, string> = {
  pending: "En cola",
  processing: "Procesando",
  done: "Listo",
  failed: "Error",
  cancelled: "Cancelado",
};

export function StatusBadge({ status }: { status: MatchStatus }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABEL[status]}</span>;
}

const COURT_LABEL: Record<CourtType, string> = {
  piso: "Pista (indoor)",
  playa: "Playa",
};

export function CourtBadge({ courtType }: { courtType: CourtType }) {
  return <span className={`badge badge-${courtType}`}>{COURT_LABEL[courtType]}</span>;
}
