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
  club/equipo. `docker-compose` lo levanta todo en dos contenedores. Migrar
  a PostgreSQL es solo cambiar `DATABASE_URL`.
- **Procesamiento en background con `BackgroundTasks`**: suficiente para
  analizar unos pocos vídeos a la vez en un único proceso. Para más carga
  concurrente, sustituir por Celery/RQ (ver ROADMAP).

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
| `app/vision/court_lines.py` | Sugerencia automática de las 4 esquinas a partir de las líneas |
| `app/analysis/session.py` | Trayectorias (+ correcciones manuales) → análisis completo; lo usan el worker, el directo y las correcciones |
| `app/analysis/physical.py` | Trayectorias → distancia/velocidad/sprints/zonas/heatmap |
| `app/analysis/tactical.py` | Trayectorias + equipos → eventos (pase, pérdida, tiro, gol) y resumen por equipo |
| `app/reports/pdf_report.py` | Estadísticas + eventos → PDF |
| `app/worker/tasks.py` | Pega todo lo anterior y persiste en BD |
| `app/db.py` | Sesiones de BD y una migración mínima que añade columnas nuevas a BDs existentes |
