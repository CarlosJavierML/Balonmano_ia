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

## 2. Asignación de equipos (clustering de color de camiseta) — ✅ hecho

Implementado en `app/analysis/teams.py`: color mediano del torso en Lab +
k-means de 2 clústeres; los tracks lejanos a ambos centroides (árbitros)
quedan sin equipo. Con ello `tactical.py` distingue **pase** de
**pérdida**, y se calcula posesión/pases/pérdidas/tiros/goles por equipo.

También hecho:
- **Porteros** (`app/analysis/roles.py`): un track que pasa ≥70 % del tiempo
  dentro de un área es portero; su equipo se deduce de a quién pasa el balón.
- **Árbitros**: tracks con color fiable que no encaja en ningún equipo; se
  excluyen de la posesión para que no "roben" el balón.
- **Correcciones manuales**: renombrar equipos y jugadores, y corregir el
  equipo/rol de un jugador. Las trayectorias se guardan
  (`data/tracking/*.json.gz`), así que los eventos se recalculan sin volver
  a procesar el vídeo.

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

## 4. Escalar el procesamiento en segundo plano — ✅ hecho

Cola persistida en la base de datos (`app/worker/`) con worker separable
del proceso web, límite de concurrencia, posición y progreso visibles,
cancelación, reintentos y recuperación de trabajos huérfanos. En Docker el
worker va en su propio contenedor. Ver "Cola de análisis" en
ARCHITECTURE.md (y por qué no Celery + Redis).

Siguiente paso si algún día hace falta: con varias máquinas worker en red,
pasar a PostgreSQL (SQLite compartido solo funciona en la misma máquina) y
guardar vídeos/trayectorias en un almacenamiento común (p. ej. S3/MinIO).

## 5. Calibración asistida — ✅ hecho

El formulario muestra un fotograma del vídeo (o una captura del stream en
directo vía `POST /matches/live/preview`), detecta automáticamente las 4
esquinas a partir de las líneas blancas de la pista
(`app/vision/court_lines.py`, `POST /calibration/suggest`) y permite
arrastrarlas para ajustarlas. Posible mejora: detección basada en rectas
(Hough) para pistas con líneas de varios colores o muy tapadas por jugadores.

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
