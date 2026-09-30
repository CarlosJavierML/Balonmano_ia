/**
 * Live analysis with this device's own camera (phone, tablet, laptop).
 *
 * The camera stream and the frame sender live at module level, not inside a
 * React component, so they keep running when the app navigates from the
 * "new session" form to the session page.
 *
 * Frames go to the server one at a time: the server answers each POST once
 * that frame has been analyzed, and only then is the next one captured. The
 * frame rate therefore adapts by itself to the server (GPU vs CPU) and the
 * connection, up to MAX_FPS.
 */
import { pushLiveFrame } from "../api/client";

/** Same rate the server analyzes RTSP cameras at (analysis_target_fps). */
const MAX_FPS = 6;
/** Frames are downscaled to this width: plenty for detection, light to upload. */
const FRAME_MAX_WIDTH = 960;
const JPEG_QUALITY = 0.75;

export interface SenderStatus {
  matchId: number | null;
  sending: boolean;
  framesSent: number;
  framesProcessed: number;
  error: string | null;
}

let stream: MediaStream | null = null;
let video: HTMLVideoElement | null = null;
let canvas: HTMLCanvasElement | null = null;
let wakeLock: { release: () => Promise<void> } | null = null;
let stopRequested = false;
let status: SenderStatus = { matchId: null, sending: false, framesSent: 0, framesProcessed: 0, error: null };
const listeners = new Set<(s: SenderStatus) => void>();

function setStatus(patch: Partial<SenderStatus>) {
  status = { ...status, ...patch };
  listeners.forEach((l) => l(status));
}

export function subscribe(listener: (s: SenderStatus) => void): () => void {
  listeners.add(listener);
  listener(status);
  return () => listeners.delete(listener);
}

export function getStatus(): SenderStatus {
  return status;
}

export function cameraSupported(): boolean {
  return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
}

export function currentStream(): MediaStream | null {
  return stream && stream.active ? stream : null;
}

/** Opens the camera (the rear one on phones) and returns its stream. */
export async function openCamera(): Promise<MediaStream> {
  if (currentStream()) return stream!;
  if (!cameraSupported()) {
    throw new Error(
      window.isSecureContext
        ? "Este navegador no permite usar la cámara."
        : "El navegador solo permite la cámara en páginas https:// (usa el enlace público de Colab).",
    );
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
  } catch (e) {
    const name = e instanceof DOMException ? e.name : "";
    if (name === "NotAllowedError") throw new Error("Permiso de cámara denegado. Actívalo en los ajustes del navegador.");
    if (name === "NotFoundError") throw new Error("No se encontró ninguna cámara en este dispositivo.");
    throw new Error(`No se pudo abrir la cámara: ${e instanceof Error ? e.message : String(e)}`);
  }
  video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  await video.play();
  if (!video.videoWidth) {
    await new Promise((resolve) => video!.addEventListener("loadeddata", resolve, { once: true }));
  }
  return stream;
}

export function closeCamera() {
  stopSending();
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  video = null;
}

/** Current camera image as a JPEG, at the exact size sent for analysis (so
 * calibration corners clicked on it match the analyzed frames). */
export async function captureFrame(): Promise<Blob> {
  if (!video || !currentStream()) throw new Error("La cámara no está abierta");
  const scale = Math.min(1, FRAME_MAX_WIDTH / video.videoWidth);
  const w = Math.round(video.videoWidth * scale);
  const h = Math.round(video.videoHeight * scale);
  canvas = canvas ?? document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  canvas.getContext("2d")!.drawImage(video, 0, 0, w, h);
  return new Promise((resolve, reject) =>
    canvas!.toBlob((b) => (b ? resolve(b) : reject(new Error("No se pudo capturar la imagen"))), "image/jpeg", JPEG_QUALITY),
  );
}

async function keepScreenOn() {
  try {
    // Screen Wake Lock API (Chrome/Edge/Safari 16.4+): a sleeping phone
    // would stop the camera.
    const nav = navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void> }> } };
    wakeLock = (await nav.wakeLock?.request("screen")) ?? null;
  } catch {
    wakeLock = null; // not supported / denied: the page shows a tip instead
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Starts sending frames to a live session (no-op if already sending to it). */
export function startSending(matchId: number) {
  if (status.sending && status.matchId === matchId) return;
  stopSending();
  stopRequested = false;
  setStatus({ matchId, sending: true, framesSent: 0, framesProcessed: 0, error: null });
  keepScreenOn();
  // Re-acquire the wake lock when the page comes back to the foreground.
  document.addEventListener("visibilitychange", onVisibility);
  void sendLoop(matchId);
}

function onVisibility() {
  if (document.visibilityState === "visible" && status.sending) keepScreenOn();
}

export function stopSending() {
  stopRequested = true;
  document.removeEventListener("visibilitychange", onVisibility);
  wakeLock?.release().catch(() => undefined);
  wakeLock = null;
  if (status.sending) setStatus({ sending: false });
}

async function sendLoop(matchId: number) {
  const t0 = performance.now();
  const minInterval = 1000 / MAX_FPS;
  while (!stopRequested) {
    const started = performance.now();
    try {
      const frame = await captureFrame();
      const t = (started - t0) / 1000;
      const processed = await pushLiveFrame(matchId, frame, t);
      setStatus({ framesSent: status.framesSent + 1, framesProcessed: processed, error: null });
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      // The session no longer exists (stopped from another device, server
      // restarted...): stop quietly instead of retrying forever.
      if (/^404|No hay una transmisión activa/.test(message)) {
        setStatus({ sending: false, error: "La sesión en directo ya no está activa." });
        return;
      }
      setStatus({ error: message });
      await sleep(1000);
    }
    const elapsed = performance.now() - started;
    if (elapsed < minInterval) await sleep(minInterval - elapsed);
  }
}
