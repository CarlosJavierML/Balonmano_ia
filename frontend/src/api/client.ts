import type {
  Job,
  QueueItem,
  LiveStatsSnapshot,
  Match,
  MatchDetail,
  CameraSetup,
  PixelCorner,
  PlayerUpdate,
  ReplayData,
  TeamNames,
} from "../types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Human-readable error from a failed response (FastAPI's `detail` if present). */
async function errorMessage(res: Response): Promise<string> {
  const body = await res.text();
  try {
    const detail = JSON.parse(body).detail;
    if (typeof detail === "string") return detail;
    if (detail) return JSON.stringify(detail);
  } catch {
    // not JSON; fall through
  }
  return `${res.status} ${res.statusText}${body ? `: ${body}` : ""}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export function listMatches(): Promise<Match[]> {
  return request<Match[]>("/matches");
}

export function getMatch(id: number): Promise<MatchDetail> {
  return request<MatchDetail>(`/matches/${id}`);
}

export function deleteMatch(id: number): Promise<void> {
  return request<void>(`/matches/${id}`, { method: "DELETE" });
}

export function renameTeams(id: number, names: TeamNames): Promise<MatchDetail> {
  return request<MatchDetail>(`/matches/${id}/teams`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names }),
  });
}

export function updatePlayer(id: number, trackId: number, update: PlayerUpdate): Promise<MatchDetail> {
  return request<MatchDetail>(`/matches/${id}/players/${trackId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
}

export async function suggestCorners(image: Blob): Promise<PixelCorner[]> {
  const form = new FormData();
  form.append("image", image, "frame.jpg");
  const res = await fetch(`${API_URL}/calibration/suggest`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await errorMessage(res));
  return (await res.json()).corners as PixelCorner[];
}

export function listJobs(includeFinished = false): Promise<QueueItem[]> {
  return request<QueueItem[]>(`/jobs${includeFinished ? "?include_finished=true" : ""}`);
}

export function cancelAnalysis(id: number): Promise<Job> {
  return request<Job>(`/matches/${id}/cancel`, { method: "POST" });
}

export function reanalyzeMatch(id: number): Promise<Job> {
  return request<Job>(`/matches/${id}/reanalyze`, { method: "POST" });
}

export function getReplay(id: number): Promise<ReplayData> {
  return request<ReplayData>(`/matches/${id}/replay`);
}

export function renderReplayVideo(id: number, speed: 1 | 2 | 4): Promise<Job> {
  return request<Job>(`/matches/${id}/replay-video`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speed }),
  });
}

export function replayVideoUrl(id: number, version: string): string {
  return `${API_URL}/matches/${id}/replay-video?v=${encodeURIComponent(version)}`;
}

export function reportUrl(id: number): string {
  return `${API_URL}/matches/${id}/report`;
}

export function heatmapUrl(matchId: number, trackId: number): string {
  return `${API_URL}/matches/${matchId}/heatmap/${trackId}`;
}

export async function uploadMatchVideo(params: {
  name: string;
  courtType: string;
  file: File;
  corners?: PixelCorner[];
}): Promise<Match> {
  const form = new FormData();
  form.append("name", params.name);
  form.append("court_type", params.courtType);
  form.append("file", params.file);
  if (params.corners) {
    form.append("calibration", JSON.stringify({ corners: params.corners }));
  }
  return request<Match>("/matches/upload", { method: "POST", body: form });
}

function calibrationFor(cam: CameraSetup) {
  return cam.corners ? { corners: cam.corners, region: cam.region } : null;
}

/** Uploads one video per camera of the same session. */
export function uploadMultiCameraMatch(params: {
  name: string;
  courtType: string;
  cameras: CameraSetup[];
}): Promise<Match> {
  const form = new FormData();
  form.append("name", params.name);
  form.append("court_type", params.courtType);
  params.cameras.forEach((cam) => form.append("file", cam.file as File));
  form.append(
    "cameras",
    JSON.stringify(
      params.cameras.map((cam, i) => ({
        name: cam.name,
        region: cam.region,
        calibration: calibrationFor(cam),
        time_offset_s: i === 0 ? 0 : cam.autoSync ? null : cam.offsetS,
      })),
    ),
  );
  return request<Match>("/matches/upload", { method: "POST", body: form });
}

export function startMultiCameraLive(params: {
  name: string;
  courtType: string;
  cameras: CameraSetup[];
}): Promise<Match> {
  return request<Match>("/matches/live/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: params.name,
      court_type: params.courtType,
      cameras: params.cameras.map((cam) => ({
        stream_url: cam.streamUrl,
        name: cam.name,
        region: cam.region,
        calibration: calibrationFor(cam),
      })),
    }),
  });
}

export function updateCamera(
  id: number,
  index: number,
  update: { name?: string; time_offset_s?: number; auto_sync?: boolean },
): Promise<MatchDetail> {
  return request<MatchDetail>(`/matches/${id}/cameras/${index}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
}

export function startLiveMatch(params: {
  name: string;
  courtType: string;
  streamUrl: string;
  corners?: PixelCorner[];
}): Promise<Match> {
  return request<Match>("/matches/live/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: params.name,
      court_type: params.courtType,
      stream_url: params.streamUrl,
      calibration: params.corners ? { corners: params.corners } : null,
    }),
  });
}

export async function fetchStreamPreview(streamUrl: string): Promise<Blob> {
  const res = await fetch(`${API_URL}/matches/live/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stream_url: streamUrl }),
  });
  if (!res.ok) throw new Error(`No se pudo obtener imagen del stream: ${await errorMessage(res)}`);
  return res.blob();
}

/** Starts a live session fed by this device's camera (see camera/browserCamera). */
export function startBrowserLiveMatch(params: {
  name: string;
  courtType: string;
  corners?: PixelCorner[];
}): Promise<Match> {
  return request<Match>("/matches/live/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: params.name,
      court_type: params.courtType,
      source: "browser",
      calibration: params.corners ? { corners: params.corners } : null,
    }),
  });
}

/** Sends one camera frame; resolves once the server has analyzed it. */
export async function pushLiveFrame(matchId: number, frame: Blob, t: number): Promise<number> {
  const res = await request<{ frames_processed: number }>(
    `/matches/live/${matchId}/frame?t=${t.toFixed(3)}`,
    { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: frame },
  );
  return res.frames_processed;
}

export function stopLiveMatch(id: number): Promise<Match> {
  return request<Match>(`/matches/live/${id}/stop`, { method: "POST" });
}

export function getLiveSnapshot(id: number): Promise<LiveStatsSnapshot> {
  return request<LiveStatsSnapshot>(`/matches/live/${id}/snapshot`);
}
