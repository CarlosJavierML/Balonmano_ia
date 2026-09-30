import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getReplay } from "../api/client";
import type { ReplayData } from "../types";

const SPEEDS = [0.5, 1, 2, 4, 8];
const TRAIL_S = 2;
const EVENT_SHOW_S = 2;
const MAX_GAP_S = 1;
const BALL_MAX_GAP_S = 0.5;

const EVENT_LABEL: Record<string, string> = {
  pase: "Pase",
  perdida: "Pérdida de balón",
  cambio_posesion: "Cambio de posesión",
  tiro: "Tiro",
  gol: "¡Gol!",
};
const EVENT_COLOR: Record<string, string> = {
  pase: "#2fbf71",
  perdida: "#ef5a6f",
  cambio_posesion: "#93a1c2",
  tiro: "#4f9de8",
  gol: "#e8b64f",
};

interface Track {
  times: Float64Array;
  xs: Float64Array;
  ys: Float64Array;
}

function toTrack(points: [number, number, number][]): Track {
  return {
    times: Float64Array.from(points, (p) => p[0]),
    xs: Float64Array.from(points, (p) => p[1]),
    ys: Float64Array.from(points, (p) => p[2]),
  };
}

/** Position at time t, linearly interpolated; null if not seen around t. */
function positionAt(track: Track, t: number, maxGap: number): [number, number] | null {
  const { times, xs, ys } = track;
  const n = times.length;
  if (n === 0 || t < times[0] - 1e-6 || t > times[n - 1] + 1e-6) return null;
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) lo = mid;
    else hi = mid;
  }
  if (Math.abs(times[lo] - t) < 1e-6 || lo === hi) return [xs[lo], ys[lo]];
  if (Math.abs(times[hi] - t) < 1e-6) return [xs[hi], ys[hi]];
  if (times[hi] - times[lo] > maxGap) return null;
  const a = (t - times[lo]) / (times[hi] - times[lo]);
  return [xs[lo] + a * (xs[hi] - xs[lo]), ys[lo] + a * (ys[hi] - ys[lo])];
}

/** Dark team colors get a light outline so the dot stands out on the court. */
function outlineFor(hex: string, goalkeeper: boolean): string {
  if (goalkeeper) return "#ffffff";
  const n = parseInt(hex.slice(1), 16);
  const luminance = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return luminance < 0.3 ? "#d8dde8" : "#111111";
}

function formatClock(t: number): string {
  const m = Math.floor(t / 60)
    .toString()
    .padStart(2, "0");
  const s = Math.floor(t % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

/** Animated top-down recreation of the session: players, ball and events. */
export default function ReplayViewer({ matchId, version }: { matchId: number; version: string }) {
  const [data, setData] = useState<ReplayData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [showTrails, setShowTrails] = useState(true);
  const [showNames, setShowNames] = useState(true);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    getReplay(matchId)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
    // `version` changes when the analysis changes (re-analysis, corrections).
  }, [matchId, version]);

  // Canvas follows the card width (phone or desktop).
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [data]);

  const prepared = useMemo(() => {
    if (!data) return null;
    const lastPoint = Math.max(0, ...data.players.map((p) => p.points[p.points.length - 1]?.[0] ?? 0));
    return {
      players: data.players.map((p) => ({ ...p, track: toTrack(p.points) })),
      ball: toTrack(data.ball),
      labels: new Map(data.players.map((p) => [p.track_id, p.label])),
      duration: Math.max(data.duration_s, lastPoint),
    };
  }, [data]);

  // Playback clock.
  useEffect(() => {
    if (!playing || !prepared) return;
    let frame = 0;
    let last = performance.now();
    const step = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setT((prev) => {
        const next = prev + dt * speed;
        if (next >= prepared.duration) {
          setPlaying(false);
          return prepared.duration;
        }
        return next;
      });
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, prepared]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data || !prepared) return;
    const { length_m: L, width_m: W } = data.court;
    const margin = 1.6; // meters around the court (goals)
    const scale = width / (L + 2 * margin);
    const height = Math.round((W + 2 * margin) * scale);
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
    }
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const px = (x: number) => (x + margin) * scale;
    const py = (y: number) => (y + margin) * scale;

    // Court.
    ctx.fillStyle = "#0e1628";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#8b5a2b";
    ctx.fillRect(px(0), py(0), L * scale, W * scale);
    ctx.strokeStyle = "#ececec";
    ctx.lineWidth = Math.max(1.5, scale * 0.08);
    ctx.strokeRect(px(0), py(0), L * scale, W * scale);
    ctx.beginPath();
    ctx.moveTo(px(L / 2), py(0));
    ctx.lineTo(px(L / 2), py(W));
    ctx.stroke();
    for (const [gx, a0, a1] of [
      [0, -Math.PI / 2, Math.PI / 2],
      [L, Math.PI / 2, (3 * Math.PI) / 2],
    ] as const) {
      ctx.beginPath();
      ctx.arc(px(gx), py(W / 2), data.court.goal_area_radius_m * scale, a0, a1);
      ctx.stroke();
      if (data.court.free_throw_radius_m) {
        ctx.setLineDash([scale * 0.5, scale * 0.5]);
        ctx.beginPath();
        ctx.arc(px(gx), py(W / 2), data.court.free_throw_radius_m * scale, a0, a1);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      const depth = gx === 0 ? -0.8 : 0.8;
      ctx.fillStyle = "#ececec";
      ctx.fillRect(
        px(Math.min(gx, gx + depth)),
        py(W / 2 - data.court.goal_width_m / 2),
        0.8 * scale,
        data.court.goal_width_m * scale,
      );
    }

    // Recent events: an expanding ring where they happened.
    for (const e of data.events) {
      const age = t - e.t;
      if (age < 0 || age > EVENT_SHOW_S || e.x === null || e.y === null) continue;
      ctx.strokeStyle = EVENT_COLOR[e.type] ?? "#ffd700";
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(px(e.x), py(e.y), scale * (0.6 + 1.2 * (age / EVENT_SHOW_S)), 0, 2 * Math.PI);
      ctx.stroke();
    }

    const radius = Math.max(5, scale * 0.45);
    const labels: [string, number, number][] = [];
    for (const p of prepared.players) {
      const pos = positionAt(p.track, t, MAX_GAP_S);
      if (!pos) continue;
      if (showTrails) {
        ctx.strokeStyle = p.color;
        ctx.globalAlpha = 0.6;
        ctx.lineWidth = 2;
        ctx.beginPath();
        let started = false;
        for (let k = 8; k >= 0; k--) {
          const q = k === 0 ? pos : positionAt(p.track, t - (TRAIL_S * k) / 8, MAX_GAP_S);
          if (!q) continue;
          if (started) ctx.lineTo(px(q[0]), py(q[1]));
          else ctx.moveTo(px(q[0]), py(q[1]));
          started = true;
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(px(pos[0]), py(pos[1]), radius, 0, 2 * Math.PI);
      ctx.fill();
      ctx.lineWidth = p.role === "portero" ? 3 : 2;
      ctx.strokeStyle = outlineFor(p.color, p.role === "portero");
      ctx.stroke();
      if (showNames) labels.push([p.label, px(pos[0]), py(pos[1]) - radius - 4]);
    }

    const ball = positionAt(prepared.ball, t, BALL_MAX_GAP_S);
    if (ball) {
      ctx.fillStyle = "#fadc28";
      ctx.strokeStyle = "#111111";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(px(ball[0]), py(ball[1]), Math.max(3, radius * 0.5), 0, 2 * Math.PI);
      ctx.fill();
      ctx.stroke();
    }

    ctx.font = `600 ${Math.max(10, Math.round(scale * 0.55))}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(0,0,0,0.8)";
    ctx.fillStyle = "#ffffff";
    for (const [text, x, y] of labels) {
      ctx.strokeText(text, x, y);
      ctx.fillText(text, x, y);
    }
  }, [data, prepared, t, width, showTrails, showNames]);

  useEffect(draw, [draw]);

  if (error) return <p className="helper-text">{error}</p>;
  if (!data || !prepared) return <p className="helper-text">Cargando la recreación…</p>;

  const currentEvent = [...data.events].reverse().find((e) => t - e.t >= 0 && t - e.t <= EVENT_SHOW_S);
  const describe = (e: ReplayData["events"][number]) => {
    const who =
      e.from !== null && e.to !== null
        ? `${prepared.labels.get(e.from) ?? "?"} → ${prepared.labels.get(e.to) ?? "?"}`
        : e.from !== null
          ? (prepared.labels.get(e.from) ?? "")
          : "";
    return [EVENT_LABEL[e.type] ?? e.type, who].filter(Boolean).join(" · ");
  };

  return (
    <div className="replay">
      <div className="replay-legend">
        {Object.values(data.teams).map((team) => (
          <span key={team!.name} className="team-chip">
            <span className="swatch" style={{ background: team!.color }} />
            {team!.name}
          </span>
        ))}
        <span className="helper-text">Portero: borde blanco grueso · 🟡 balón</span>
      </div>
      <div ref={wrapRef} className="replay-canvas-wrap">
        <canvas ref={canvasRef} aria-label="Recreación animada de la sesión" />
        {currentEvent && (
          <div className="replay-banner" style={{ borderColor: EVENT_COLOR[currentEvent.type] }}>
            {describe(currentEvent)}
          </div>
        )}
      </div>

      <div className="replay-timeline">
        {data.events.map((e, i) => (
          <button
            key={i}
            type="button"
            className="replay-marker"
            title={`${formatClock(e.t)} · ${describe(e)}`}
            style={{ left: `${(e.t / prepared.duration) * 100}%`, background: EVENT_COLOR[e.type] }}
            onClick={() => setT(Math.max(0, e.t - 2))}
          />
        ))}
        <input
          type="range"
          min={0}
          max={prepared.duration}
          step={0.05}
          value={t}
          onChange={(e) => setT(Number(e.target.value))}
          aria-label="Posición en la recreación"
        />
      </div>

      <div className="replay-controls">
        <button
          type="button"
          className="btn btn-small btn-primary"
          onClick={() => {
            if (t >= prepared.duration) setT(0);
            setPlaying((p) => !p);
          }}
        >
          {playing ? "❚❚ Pausa" : "▶ Reproducir"}
        </button>
        <span className="replay-clock">
          {formatClock(t)} / {formatClock(prepared.duration)}
        </span>
        <label className="replay-option">
          Velocidad
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                x{s}
              </option>
            ))}
          </select>
        </label>
        <label className="replay-option">
          <input type="checkbox" checked={showTrails} onChange={(e) => setShowTrails(e.target.checked)} /> Estelas
        </label>
        <label className="replay-option">
          <input type="checkbox" checked={showNames} onChange={(e) => setShowNames(e.target.checked)} /> Nombres
        </label>
      </div>
      {data.events.length > 0 && (
        <p className="helper-text">Toca una marca de colores de la línea de tiempo para saltar a ese evento.</p>
      )}
    </div>
  );
}
