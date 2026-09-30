export type CourtType = "piso" | "playa";
export type SourceMode = "upload" | "live";
export type MatchStatus = "pending" | "processing" | "done" | "failed" | "cancelled";
export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: number;
  match_id: number;
  /** "video": vision analysis of an upload; "tracking": saved trajectories (live). */
  kind: "video" | "tracking";
  status: JobStatus;
  attempts: number;
  max_attempts: number;
  cancel_requested: boolean;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** 1-based position in the queue while queued. */
  position: number | null;
}

export interface QueueItem extends Job {
  match_name: string;
  progress: number;
}

export interface Match {
  id: number;
  name: string;
  court_type: CourtType;
  source_mode: SourceMode;
  status: MatchStatus;
  duration_s: number | null;
  progress: number;
  /** Live sessions: IP camera URL or this device's camera. */
  live_source: "rtsp" | "browser" | null;
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
  active_job: Job | null;
  /** Empty for single-camera sessions. */
  cameras: CameraInfo[];
  fusion_report: FusionReport | null;
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
  frames_processed: number;
  /** False while the AI model is still loading at the start of the session. */
  ai_ready: boolean;
  source_connected: boolean;
  source_error: string | null;
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

export type CourtRegion = "full" | "left" | "right";

export interface SyncInfo {
  /** reference = camera 0; audio = detected; manual = typed by the user;
   * clock = live streams sharing the server clock; none = could not sync. */
  method: "reference" | "audio" | "manual" | "clock" | "none";
  offset_s: number;
  confidence?: number;
  detected_offset_s?: number;
  reason?: string;
}

export interface CameraInfo {
  index: number;
  name: string;
  region: CourtRegion;
  calibrated: boolean;
  time_offset_s: number | null;
  sync_info: SyncInfo | null;
  source: "video" | "stream";
}

export interface FusionReport {
  cameras: { index: number; region: CourtRegion; offset_s: number; tracks: number }[];
  overlap_merges: number;
  handoffs: number;
  people: number;
}

/** One camera in the "new session" form. */
export interface CameraSetup {
  key: number;
  name: string;
  region: CourtRegion;
  file: File | null;
  streamUrl: string;
  corners?: PixelCorner[];
  calibrationIncomplete: boolean;
  /** Uploads: detect the offset from the audio (else use offsetS). */
  autoSync: boolean;
  offsetS: number;
}
