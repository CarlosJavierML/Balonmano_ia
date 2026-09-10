# Hoja de ruta

El MVP actual es un pipeline heurístico completo y funcional (subida de
vídeo + directo, físico + táctico, informe PDF). Estas son las mejoras con
más impacto, ordenadas aproximadamente por relación esfuerzo/beneficio.

## 1. Modelo de detección específico de balonmano

**Problema actual**: se usa YOLOv8 pre-entrenado en COCO, cuya clase
"sports ball" no está afinada para el balón de balonmano (tamaño, textura,
oclusiones por las manos del jugador) y no distingue porteros, árbitros ni
banquillo.

**Plan**:
1. Etiquetar un dataset pequeño (200-500 frames) de vuestros propios
   vídeos con [Roboflow](https://roboflow.com) o [CVAT](https://cvat.ai/),
   con clases: `jugador`, `portero`, `arbitro`, `balon`.
2. Fine-tuning de un YOLOv8 (`yolo train`) partiendo del checkpoint actual.
3. Sustituir `settings.yolo_model_path` por el nuevo checkpoint — el resto
   del pipeline no cambia (misma interfaz `Detector.detect()`).

## 2. Asignación de equipos (clustering de color de camiseta)

Con esto se puede distinguir un **pase** (posesión pasa a un compañero) de
una **pérdida de posesión** (pasa a un rival), y calcular estadísticas por
equipo (posesión total, eficacia de tiro, etc.).

**Plan**: extraer el color dominante de la camiseta en el recorte de cada
detección de jugador (k-means sobre el tercio superior del bounding box) y
agrupar en 2-3 clústeres (equipo A / equipo B / árbitro). Guardar el
`team_label` en cada `TrackFrame` y usarlo en `app/analysis/tactical.py`.

## 3. Detección de eventos con un modelo entrenado (acción/vídeo)

Las reglas actuales (velocidad + proximidad a portería) funcionan como
baseline pero fallarán en casos ambiguos (pase largo rápido vs. tiro,
rechace del portero vs. gol). Cuando haya suficientes clips etiquetados:

- Entrenar un clasificador de acciones sobre ventanas cortas de la
  trayectoria del balón (features: velocidad, aceleración, distancia a
  portería, proximidad a jugadores) — un modelo simple (gradient boosting /
  LSTM pequeño) ya mejoraría mucho sobre reglas fijas.
- El contrato de salida (`TacticalEvent`) ya está pensado para que este
  modelo sustituya a `detect_events()` sin tocar BD, API ni informes.

## 4. Escalar el procesamiento en segundo plano

`BackgroundTasks` de FastAPI es síncrono a nivel de proceso: si suben dos
vídeos largos a la vez, se compite por CPU/GPU. Para varios equipos usando
la app a la vez:

- Mover `process_uploaded_video` / `finalize_live_session` a Celery + Redis
  (o RQ), con un worker dedicado (idealmente con GPU) separado del proceso
  web.
- Esto también permite reintentos automáticos y una cola visible del
  estado de cada análisis.

## 5. Calibración asistida

Hoy la calibración es manual (introducir 4 puntos en píxeles a mano). Se
podría:

- Mostrar el primer frame del vídeo en el formulario de subida y dejar al
  usuario hacer clic directamente sobre las 4 esquinas (en vez de escribir
  coordenadas).
- Detectar automáticamente las líneas de la pista (Hough transform sobre
  las líneas blancas) como punto de partida sugerido.

## 6. GPU / rendimiento en directo

El streaming en directo usa la CPU por defecto. Con GPU disponible,
`ultralytics` la usa automáticamente si PyTorch detecta CUDA — solo hace
falta instalar la build de PyTorch con soporte CUDA correspondiente al
hardware del club. Para RTSP de alta resolución, considerar bajar la
resolución de captura o el `analysis_target_fps` en `app/config.py`.

## 7. Multi-cámara

La arquitectura actual asume una única cámara fija. Para pistas con varias
cámaras (p. ej. una por cada mitad de pista), el siguiente paso natural es
fusionar trayectorias de varias fuentes usando la misma homografía por
cámara y reconciliar IDs de tracking entre cámaras (re-identificación).
