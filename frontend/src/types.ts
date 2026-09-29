export type CourtType = "piso" | "playa";
export type SourceMode = "upload" | "live";
export type MatchStatus = "pending" | "processing" | "done" | "failed";

export interface Match {
  id: number;
  name: string;
  court_type: CourtType;
  source_mode: SourceMode;
  status: MatchStatus;
  duration_s: number | null;
  progress: number;
  error_message: string | null;
  created_at: string;
}

export interface PlayerStat {
  track_id: number;
  player_id: number | null;
  label: string;
  team: TeamLabel | null;
  role: PlayerRole | null;
  distance_m: number;
  avg_speed_kmh: number;
  max_speed_kmh: number;
  sprint_count: number;
  time_in_zones: Record<string, number>;
  heatmap_path: string | null;
}

export type TeamLabel = "A" | "B";
export type PlayerRole = "jugador" | "portero" | "arbitro";
export type TeamNames = Partial<Record<TeamLabel, string>>;

export interface TeamSummaryEntry {
  color: string | null;
  players: number;
  possession_s: number;
  possession_pct: number;
  pases: number;
  perdidas: number;
  tiros: number;
  goles: number;
}

export type TeamSummary = Partial<Record<TeamLabel, TeamSummaryEntry>>;

export type EventType = "pase" | "perdida" | "cambio_posesion" | "tiro" | "gol";

export interface MatchEvent {
  event_type: EventType;
  timestamp_s: number;
  track_id_from: number | null;
  track_id_to: number | null;
  x: number | null;
  y: number | null;
  meta: Record<string, unknown>;
}

export interface MatchDetail extends Match {
  team_summary: TeamSummary | null;
  team_names: TeamNames | null;
  /** Whether team/role corrections can be applied (trajectories saved). */
  editable: boolean;
  player_stats: PlayerStat[];
  events: MatchEvent[];
}

export interface LiveStatsSnapshot {
  match_id: number;
  elapsed_s: number;
  active_tracks: number;
  recent_events: MatchEvent[];
  player_stats: PlayerStat[];
  team_summary: TeamSummary | null;
}

export interface PixelCorner {
  x: number;
  y: number;
}

export interface PlayerUpdate {
  label?: string;
  team?: TeamLabel | null;
  role?: PlayerRole;
  reset?: boolean;
}
