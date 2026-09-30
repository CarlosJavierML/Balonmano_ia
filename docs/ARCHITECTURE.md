# Arquitectura

## Flujo de datos

```
Vídeo subido / Stream RTSP
        │
        ▼
  Detector (YOLOv8)  ──► personas + balón por frame
        │
        ▼
  Tracker
   ├─ Jugadores: ByteTrack (supervision) → track_id estable
   └─ Balón: seguimiento por proximidad + suavizado (sin ID múltiple)
        │
        ▼
  Calibración de pista (homografía) → coordenadas mundo (metros)
        │
        ├──► app/analysis/physical.py  → distancia, velocidad, sprints, zonas, heatmap
        ├──► app/analysis/teams.py     → equipo A/B por color de camiseta (Lab)
        ├──► app/analysis/roles.py     → portero (por posición) / árbitro (color atípico)
        └──► app/analysis/tactical.py  → posesión por equipo, pases, pérdidas, tiros, goles
        │
        ▼
  Persistencia (SQLite vía SQLAlchemy async): Match, PlayerMatchStat, Event
        │
        ├──► API REST (FastAPI) → dashboard (React)
        └──► Informe PDF (reportlab + matplotlib)
```

## Por qué estas decisiones

- **YOLOv8 + ByteTrack (vía `supervision`)**: es la combinación estándar de
  facto para detección+tracking multi-objeto en tiempo real, con modelos
  pre-entrenados listos para usar (clase "person" y "sports ball" de COCO),
  lo que permite tener un pipeline funcionando sin depender de entrenar un
  modelo propio desde el primer día.
- **Homografía de 4 puntos** para pasar de píxeles a metros: es la técnica
  estándar para cámaras fijas mirando un plano (el suelo de la pista). Con
  los 4 puntos de referencia calculamos una transformación de perspectiva
  que corrige la distorsión de perspectiva de una cámara no cenital.
- **Detección de eventos tácticos basada en reglas**: no requiere datos de
  entrenamiento etiquetados y da una baseline inspeccionable y depurable
  desde el primer despliegue. El formato de salida (`TacticalEvent`) está
  diseñado para que un futuro modelo entrenado pueda sustituir a las reglas
  sin tocar el resto de la aplicación (BD, API, informes).
- **FastAPI + SQLAlchemy async + SQLite**: ligero y suficiente para un
  club/equipo (en modo WAL, para que web y worker la compartan). Migrar a
  PostgreSQL es solo cambiar `DATABASE_URL`.
- **Cola de análisis en la propia base de datos** (tabla `jobs`) en vez de
  Celery + Redis: da lo mismo que necesita un club (orden de llegada,
  posición visible, progreso, cancelar, reintentos, recuperación si el
  worker se cae, varios workers) sin añadir otro servicio que mantener. Un
  worker reclama un trabajo con un `UPDATE ... WHERE status='queued'`
  condicional, así que varios workers nunca cogen el mismo. Por defecto el
  worker corre dentro del servidor web; en Docker va en su propio
  contenedor (`python -m app.worker`).

## Multi-cámara

```
vídeo cámara 1 ─► pipeline (región izq.) ─► trayectorias cam 1 ─┐
vídeo cámara 2 ─► pipeline (región der.) ─► trayectorias cam 2 ─┤
                                                                ▼
                    desfase por cámara (audio / manual / reloj del directo)
                                                                ▼
             fusión: rejilla temporal común + unión en zona compartida
                     + relevos entre cámaras ─► trayectorias de la sesión
                                                                ▼
                     mismo análisis que con una cámara (stats, equipos, eventos)
```

Las trayectorias de cada cámara se guardan por separado, así que cambiar la
sincronización solo repite la fusión (trabajo `tracking`), no la visión.
Las sesiones de una cámara no pasan por la fusión y funcionan igual que antes.

## Cola de análisis

```
subida de vídeo / fin de directo ──► jobs (status=queued)
                                        │  worker: claim atómico (1 a la vez por defecto)
                                        ▼
                                  status=running ──► latido cada 5 s: progreso,
                                        │            heartbeat_at, ¿cancelación?
               ┌────────────────────────┼─────────────────────────┐
               ▼                        ▼                         ▼
             done           error → queued (reintento)       cancelled
                            o failed si no quedan intentos
```

- Un trabajo `running` sin latido durante `job_stale_after_s` (worker caído
  o reiniciado) vuelve a la cola, o pasa a `failed` si agotó los intentos.
- Errores que no se arreglan reintentando (vídeo ilegible, fichero borrado)
  no gastan reintentos.
- La cancelación de un trabajo en curso llega al pipeline a través del
  callback de progreso, que se llama tras cada fotograma analizado.
- Tipos de trabajo: `video` (pipeline de visión sobre el vídeo subido) y
  `tracking` (análisis de las trayectorias guardadas de un directo).

## Módulos clave del backend

| Módulo | Responsabilidad |
|---|---|
| `app/court.py` | Dimensiones de pista/playa, homografía píxel↔metros |
| `app/vision/detector.py` | Envoltorio de YOLOv8 |
| `app/vision/tracker.py` | ByteTrack para jugadores, tracker propio para el balón |
| `app/vision/pipeline.py` | `FrameProcessor` (detección + tracking + color de camiseta por frame) y el recorrido de un vídeo completo con progreso |
| `app/vision/live.py` | Usa el mismo `FrameProcessor` de forma incremental, en un hilo, para RTSP; captura de fotograma para calibrar |
| `app/analysis/teams.py` | Color de camiseta por jugador → equipo A/B (o sin equipo) |
| `app/analysis/roles.py` | Porteros (tiempo en el área + pases) y árbitros |
| `app/vision/fusion.py` | Multi-cámara: une las trayectorias de varias cámaras en una sola sesión |
| `app/vision/audio_sync.py` | Desfase entre cámaras a partir de su audio (ffmpeg + correlación) |
| `app/vision/court_lines.py` | Sugerencia automática de las 4 esquinas a partir de las líneas |
| `app/analysis/session.py` | Trayectorias (+ correcciones manuales) → análisis completo; lo usan el worker, el directo y las correcciones |
| `app/analysis/physical.py` | Trayectorias → distancia/velocidad/sprints/zonas/heatmap |
| `app/analysis/tactical.py` | Trayectorias + equipos → eventos (pase, pérdida, tiro, gol) y resumen por equipo |
| `app/reports/pdf_report.py` | Estadísticas + eventos → PDF |
| `app/analysis/replay.py` | Trayectorias + equipos + eventos → datos de la recreación animada (`GET /matches/{id}/replay`) |
| `app/reports/replay_video.py` | Recreación → vídeo MP4 H.264 (trabajo `replay_video` de la cola; no cambia el estado de la sesión) |
| `app/worker/queue.py` | Operaciones de la cola: encolar, reclamar, recuperar huérfanos, cancelar |
| `app/worker/runner.py` | El worker: concurrencia, latidos/progreso, cancelación, reintentos |
| `app/worker/tasks.py` | Qué hace cada tipo de trabajo; pega todo lo anterior y persiste en BD |
| `app/db.py` | Sesiones de BD y una migración mínima que añade columnas nuevas a BDs existentes |
