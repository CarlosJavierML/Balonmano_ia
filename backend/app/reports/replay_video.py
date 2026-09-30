"""Renders the 2D recreation of a session (see app.analysis.replay) as an
MP4 video: top-down court, players as colored dots with their names and a
short trail, the ball, and a banner for each tactical event.

Shapes are drawn with OpenCV (fast); text with Pillow, because OpenCV's
built-in fonts can't draw accents (Pérdida, Lucía...). Each distinct text
(names, clock, event banners) is rendered once into a small RGBA "sprite"
and alpha-blended into the frames, so no per-frame Pillow work is needed.
Frames are piped to ffmpeg and encoded as H.264, which plays everywhere
(phones, WhatsApp).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.vision.audio_sync import ffmpeg_executable

WIDTH, HEIGHT = 1280, 720
OUTPUT_FPS = 25
HEADER_H = 78
MARGIN = 36
TRAIL_S = 2.0
EVENT_BANNER_S = 2.0
# A person/ball is drawn only if seen within this time of the current instant.
MAX_GAP_S = 1.0
BALL_MAX_GAP_S = 0.5

FLOOR = (46, 86, 139)  # BGR, warm court color
LINE = (235, 235, 235)
BACKGROUND = (32, 22, 14)
BALL = (40, 220, 250)

EVENT_TEXT = {
    "pase": "Pase",
    "perdida": "Pérdida de balón",
    "cambio_posesion": "Cambio de posesión",
    "tiro": "Tiro",
    "gol": "¡GOL!",
}


def _bgr(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return (b, g, r)


@lru_cache(maxsize=4)
def _font(size: int) -> ImageFont.ImageFont:
    try:
        import matplotlib

        path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans-Bold.ttf"
        return ImageFont.truetype(str(path), size)
    except Exception:  # noqa: BLE001 - fall back to Pillow's built-in font
        return ImageFont.load_default()


class _Geometry:
    def __init__(self, court: dict) -> None:
        self.length, self.width = court["length_m"], court["width_m"]
        avail_w, avail_h = WIDTH - 2 * MARGIN, HEIGHT - HEADER_H - 2 * MARGIN
        self.scale = min(avail_w / self.length, avail_h / self.width)
        self.x0 = (WIDTH - self.length * self.scale) / 2
        self.y0 = HEADER_H + (HEIGHT - HEADER_H - self.width * self.scale) / 2

    def px(self, x: float, y: float) -> tuple[int, int]:
        return int(round(self.x0 + x * self.scale)), int(round(self.y0 + y * self.scale))


def _court_background(court: dict, geo: _Geometry) -> np.ndarray:
    img = np.full((HEIGHT, WIDTH, 3), BACKGROUND, dtype=np.uint8)
    L, W = court["length_m"], court["width_m"]
    cv2.rectangle(img, geo.px(0, 0), geo.px(L, W), FLOOR, -1)
    thick = 2
    cv2.rectangle(img, geo.px(0, 0), geo.px(L, W), LINE, thick, cv2.LINE_AA)
    cv2.line(img, geo.px(L / 2, 0), geo.px(L / 2, W), LINE, thick, cv2.LINE_AA)
    r6 = int(court["goal_area_radius_m"] * geo.scale)
    r9 = int((court.get("free_throw_radius_m") or 0) * geo.scale)
    for goal_x, start, end in ((0.0, -90, 90), (L, 90, 270)):
        center = geo.px(goal_x, W / 2)
        # Goal area (6 m) and free-throw line (9 m, dashed).
        cv2.ellipse(img, center, (r6, r6), 0, start, end, LINE, thick, cv2.LINE_AA)
        if r9:
            for a in range(start, end, 8):
                cv2.ellipse(img, center, (r9, r9), 0, a, a + 4, LINE, thick, cv2.LINE_AA)
        # Goal, drawn just outside the goal line.
        half_goal = court["goal_width_m"] / 2
        depth = 0.8 if goal_x == 0 else -0.8
        cv2.rectangle(
            img, geo.px(goal_x - depth, W / 2 - half_goal), geo.px(goal_x, W / 2 + half_goal), LINE, -1
        )
    return img


class _Sprites:
    """Cache of text rendered once as RGBA images, pasted with alpha."""

    def __init__(self) -> None:
        self._cache: dict[tuple, np.ndarray] = {}

    def text(self, text: str, size: int, color=(255, 255, 255), outline: bool = True, box=None) -> np.ndarray:
        key = (text, size, color, outline, box)
        if key not in self._cache:
            font = _font(size)
            pad = 12 if box else 3
            w = int(font.getlength(text)) + 2 * pad + 4
            h = size + 2 * pad + 6
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            if box:
                draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=10, fill=box)
            draw.text(
                (pad, pad), text, font=font, fill=color,
                stroke_width=2 if outline else 0, stroke_fill=(0, 0, 0),
            )  # fmt: skip
            rgba = np.asarray(img).astype(np.float32)
            # To BGR + alpha in [0, 1].
            self._cache[key] = np.dstack([rgba[:, :, 2::-1], rgba[:, :, 3:] / 255.0])
        return self._cache[key]

    @staticmethod
    def paste(frame: np.ndarray, sprite: np.ndarray, x: int, y: int, anchor: str = "lt") -> None:
        h, w = sprite.shape[:2]
        if anchor[0] == "m":
            x -= w // 2
        elif anchor[0] == "r":
            x -= w
        if anchor[1] == "b":
            y -= h
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, frame.shape[1]), min(y + h, frame.shape[0])
        if x0 >= x1 or y0 >= y1:
            return
        sp = sprite[y0 - y : y1 - y, x0 - x : x1 - x]
        alpha = sp[:, :, 3:]
        region = frame[y0:y1, x0:x1].astype(np.float32)
        frame[y0:y1, x0:x1] = (sp[:, :, :3] * alpha + region * (1 - alpha)).astype(np.uint8)


def _interp(points: np.ndarray, t: float, max_gap: float) -> tuple[float, float] | None:
    """Position at time t, linearly interpolated; None if not seen around t."""
    times = points[:, 0]
    if len(times) == 0 or t < times[0] - 1e-6 or t > times[-1] + 1e-6:
        return None
    j = int(np.searchsorted(times, t))
    if j < len(times) and abs(times[j] - t) < 1e-6:
        return float(points[j, 1]), float(points[j, 2])
    lo, hi = max(j - 1, 0), min(j, len(times) - 1)
    if times[hi] - times[lo] > max_gap:
        return None
    if hi == lo:
        return float(points[lo, 1]), float(points[lo, 2])
    a = (t - times[lo]) / (times[hi] - times[lo])
    return (
        float(points[lo, 1] + a * (points[hi, 1] - points[lo, 1])),
        float(points[lo, 2] + a * (points[hi, 2] - points[lo, 2])),
    )


def _clock(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def render_replay_video(
    replay: dict,
    out_path: Path,
    speed: float = 1.0,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Renders the recreation to `out_path` (MP4). `speed` > 1 speeds it up.
    `on_progress` gets the fraction done and may raise to abort."""
    ffmpeg = ffmpeg_executable()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg no está disponible para generar el vídeo")

    geo = _Geometry(replay["court"])
    background = _court_background(replay["court"], geo)
    players = [
        {**p, "arr": np.asarray(p["points"], dtype=np.float64).reshape(-1, 3), "bgr": _bgr(p["color"])}
        for p in replay["players"]
    ]
    ball = np.asarray(replay["ball"], dtype=np.float64).reshape(-1, 3)
    labels = {p["track_id"]: p["label"] for p in replay["players"]}
    events = replay["events"]
    duration = max(replay["duration_s"], max((p["arr"][-1, 0] for p in players if len(p["arr"])), default=0.0))
    total_frames = max(1, int(duration / speed * OUTPUT_FPS))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".part.mp4")
    cmd = [
        ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(OUTPUT_FPS), "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp_path),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    sprites = _Sprites()
    # Static header: title and team legend, drawn once into the background.
    sprites.paste(background, sprites.text(replay["name"][:48], 24, outline=False), MARGIN - 3, 8)
    legend_x = MARGIN
    for info in replay["teams"].values():
        cv2.rectangle(background, (legend_x, 48), (legend_x + 14, 62), _bgr(info["color"]), -1)
        cv2.rectangle(background, (legend_x, 48), (legend_x + 14, 62), (20, 20, 20), 1)
        label = sprites.text(info["name"][:20], 15, (225, 225, 225), outline=False)
        sprites.paste(background, label, legend_x + 20, 44)
        legend_x += 20 + label.shape[1] + 24
    try:
        for i in range(total_frames):
            t = i * speed / OUTPUT_FPS
            frame = background.copy()

            labels_to_draw: list[tuple[str, tuple[int, int]]] = []
            for p in players:
                pos = _interp(p["arr"], t, MAX_GAP_S)
                if pos is None:
                    continue
                # Trail: where the player was during the last seconds.
                trail = [
                    geo.px(*q)
                    for k in range(8, 0, -1)
                    if (q := _interp(p["arr"], t - TRAIL_S * k / 8, MAX_GAP_S)) is not None
                ]
                if len(trail) > 1:
                    cv2.polylines(frame, [np.array(trail + [geo.px(*pos)])], False, p["bgr"], 2, cv2.LINE_AA)
                center = geo.px(*pos)
                cv2.circle(frame, center, 11, p["bgr"], -1, cv2.LINE_AA)
                goalkeeper = p.get("role") == "portero"
                dark = sum(w * c for w, c in zip((0.114, 0.587, 0.299), p["bgr"])) / 255 < 0.3
                ring = (255, 255, 255) if goalkeeper else ((215, 220, 230) if dark else (20, 20, 20))
                cv2.circle(frame, center, 11, ring, 3 if goalkeeper else 2, cv2.LINE_AA)
                labels_to_draw.append((p["label"][:14], center))

            ball_pos = _interp(ball, t, BALL_MAX_GAP_S)
            if ball_pos is not None:
                cv2.circle(frame, geo.px(*ball_pos), 6, BALL, -1, cv2.LINE_AA)
                cv2.circle(frame, geo.px(*ball_pos), 6, (20, 20, 20), 1, cv2.LINE_AA)

            recent = [e for e in events if 0 <= t - e["t"] <= EVENT_BANNER_S]
            for e in recent:
                if e["x"] is not None and e["y"] is not None:
                    radius = 14 + int(20 * (t - e["t"]) / EVENT_BANNER_S)
                    cv2.circle(frame, geo.px(e["x"], e["y"]), radius, (0, 215, 255), 2, cv2.LINE_AA)

            for text, (x, y) in labels_to_draw:
                sprites.paste(frame, sprites.text(text, 15), x, y - 14, anchor="mb")
            clock = _clock(t) + (f"  x{speed:g}" if speed != 1 else "")
            sprites.paste(frame, sprites.text(clock, 26, outline=False), WIDTH - MARGIN + 3, 10, anchor="rt")
            if recent:
                e = recent[-1]
                parts = [EVENT_TEXT.get(e["type"], e["type"])]
                if e["from"] is not None and e["to"] is not None:
                    parts.append(f"{labels.get(e['from'], '?')} → {labels.get(e['to'], '?')}")
                elif e["from"] is not None:
                    parts.append(labels.get(e["from"], "?"))
                banner = sprites.text(" · ".join(parts), 22, (255, 215, 0), outline=False, box=(15, 15, 15, 235))
                sprites.paste(frame, banner, WIDTH // 2, HEIGHT - MARGIN - 6, anchor="mb")

            proc.stdin.write(frame.tobytes())
            if on_progress is not None and i % OUTPUT_FPS == 0:
                on_progress(i / total_frames)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg falló: {proc.stderr.read().decode()[-500:]}")
        tmp_path.replace(out_path)
    except BaseException:
        proc.kill()
        proc.wait()
        tmp_path.unlink(missing_ok=True)
        raise
