# Balonmano IA

Plataforma de análisis de entrenamientos y partidos de balonmano (pista y
playa) a partir de una **cámara fija**. Usa visión por computador para
detectar y seguir a los jugadores y el balón, calcula estadísticas físicas
(distancia, velocidad, sprints, mapas de calor, tiempo por zonas) y detecta
eventos tácticos (posesión, tiros, goles), todo consultable en un dashboard
web y exportable a un informe PDF.

Soporta tanto **subir un vídeo grabado** como **conectar una cámara/stream
en directo (RTSP)**.

## Arquitectura

```
balonmano_ia/
├── backend/          FastAPI + visión por computador (YOLOv8 + ByteTrack)
│   └── app/
│       ├── vision/       Detección, tracking, pipeline de vídeo y directo
│       ├── analysis/     Estadísticas físicas + eventos tácticos (heurística)
│       ├── reports/      Generación de informes PDF
│       ├── api/routes/   Endpoints REST
│       └── worker/       Procesamiento en segundo plano
├── frontend/         React + Vite + TypeScript (dashboard)
└── docs/             Arquitectura y hoja de ruta detalladas
```

Ver [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) para el detalle técnico
del pipeline (cómo se pasa de píxeles a metros, cómo se detectan los
eventos, etc.) y [`docs/ROADMAP.md`](docs/ROADMAP.md) para el plan de
mejora (modelos entrenados específicamente para balonmano, asignación de
equipos, etc.).

## Cómo funciona (resumen)

1. **Detección**: YOLOv8 detecta personas y el balón en cada frame muestreado.
2. **Tracking**: ByteTrack asigna un ID estable a cada jugador entre frames.
3. **Calibración de pista**: 4 puntos de referencia (las esquinas de la
   pista vistas por tu cámara) permiten transformar coordenadas de píxel a
   metros reales sobre la pista (homografía). Sin calibrar, la app asume
   que la cámara encuadra la pista completa borde a borde (funciona, pero
   es menos precisa en los laterales).
4. **Estadísticas físicas**: a partir de las trayectorias en metros se
   calcula distancia recorrida, velocidad media/máxima, sprints y mapas de
   calor por jugador.
5. **Equipos**: el color de la camiseta de cada jugador (tercio superior
   del recorte, en espacio de color Lab) se agrupa en dos equipos con
   k-means; los porteros se reconocen por pasar casi todo el tiempo en su
   área y los árbitros quedan sin equipo (y no cuentan para la posesión).
   Desde el dashboard puedes renombrar equipos y jugadores y corregir el
   equipo o rol de cualquiera: las estadísticas se recalculan al momento.
6. **Eventos tácticos**: reglas sobre la posición/velocidad del balón
   respecto a los jugadores y las porterías detectan pases (entre
   compañeros), pérdidas de balón (al rival), tiros y goles, además del
   % de posesión de cada equipo.
7. **Informe**: todo se persiste en base de datos y se puede descargar como
   PDF con gráficas, tablas y mapas de calor.

## Puesta en marcha rápida (Docker)

```bash
docker compose up --build
```

- Backend (API): http://localhost:8000 (docs interactivas en `/docs`)
- Frontend (dashboard): http://localhost:8080

## Desarrollo local sin Docker

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Requiere Python 3.10+. La primera vez que se analiza un vídeo, `ultralytics`
descarga automáticamente los pesos del modelo YOLOv8 (`yolov8n.pt`, unos
6 MB) si no están ya en caché local.

Para ejecutar los tests (no requieren descargar el modelo ni un vídeo real):

```bash
pytest
```

### Frontend

```bash
cd frontend
cp .env.example .env   # ajusta VITE_API_URL si el backend no está en localhost:8000
npm install
npm run dev
```

Abre http://localhost:5173.

## Uso

1. En el dashboard, pulsa **Nueva sesión**.
2. Elige la modalidad (pista o playa).
3. Elige **Subir vídeo** (arrastra el archivo grabado con tu cámara fija) o
   **En directo** (introduce la URL RTSP de tu cámara).
4. (Recomendado) Activa la **calibración** y pulsa **Marcar sobre la imagen**:
   la app detecta las 4 esquinas de la pista en un fotograma de tu vídeo (o
   una captura de la cámara en directo); revísalas y arrastra las que no
   encajen, o haz clic en ellas a mano. Arriba/abajo son las bandas y
   izquierda/derecha las líneas de gol. Mejora mucho la precisión de distancias y velocidades.
5. Espera a que el análisis termine (con barra de progreso) (o, en directo, observa las
   estadísticas en tiempo real y pulsa **Detener transmisión** cuando
   acabes).
6. Consulta el dashboard de la sesión y descarga el **informe en PDF**.

## Limitaciones actuales y hoja de ruta

Este proyecto arranca con un pipeline **heurístico y funcional de extremo a
extremo**, pensado para ser mejorado de forma incremental sin rehacer la
arquitectura:

- La detección de balón usa la clase genérica "sports ball" de un modelo
  YOLOv8 pre-entrenado en COCO (sin entrenamiento específico todavía), por
  lo que puede perder el balón en momentos de oclusión o mucho movimiento.
- Los eventos tácticos (pase, tiro, gol) se detectan con reglas basadas en
  posición/velocidad, no con un modelo entrenado en acciones de balonmano.
- La asignación de equipos por color de camiseta asume dos equipaciones
  bien diferenciadas; con colores parecidos algunos jugadores pueden quedar
  sin equipo (se pueden corregir a mano desde el dashboard).

Todo esto está detallado, con plan concreto de mejora, en
[`docs/ROADMAP.md`](docs/ROADMAP.md).
