import type {
  LiveStatsSnapshot,
  Match,
  MatchDetail,
  PixelCorner,
} from "../types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
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
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new Error(`No se pudo obtener imagen del stream: ${detail}`);
  }
  return res.blob();
}

export function stopLiveMatch(id: number): Promise<Match> {
  return request<Match>(`/matches/live/${id}/stop`, { method: "POST" });
}

export function getLiveSnapshot(id: number): Promise<LiveStatsSnapshot> {
  return request<LiveStatsSnapshot>(`/matches/live/${id}/snapshot`);
}
