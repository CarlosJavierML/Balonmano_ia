import type {
  LiveStatsSnapshot,
  Match,
  MatchDetail,
  PixelCorner,
  PlayerUpdate,
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

export function stopLiveMatch(id: number): Promise<Match> {
  return request<Match>(`/matches/live/${id}/stop`, { method: "POST" });
}

export function getLiveSnapshot(id: number): Promise<LiveStatsSnapshot> {
  return request<LiveStatsSnapshot>(`/matches/live/${id}/snapshot`);
}
