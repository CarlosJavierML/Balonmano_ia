# Despliegue en la nube

Tres formas de probar la app desde cualquier sitio (móvil, ordenador del
club...), de más fácil a más completa:

| Opción | Coste | Ventajas | Limitaciones |
|---|---|---|---|
| **A. Google Colab** (recomendada para probar) | Gratis, sin tarjeta | GPU gratis (análisis rápido), datos en tu Drive | Solo mientras la pestaña está abierta; vídeos >100 MB por Drive |
| **B. Tu ordenador + enlace público** | Gratis | Sin límites de tiempo; funciona el directo con las cámaras del pabellón | El ordenador tiene que estar encendido |
| **C. Hugging Face Spaces** | 9 $/mes (plan PRO) | Siempre disponible, se actualiza solo en cada push | Desde julio de 2026 los Spaces Docker requieren PRO |

## Opción A — Google Colab (gratis, con GPU)

1. Abre el cuaderno `deploy/colab/Balonmano_IA.ipynb` en Colab. La forma
   más fiable: entra en <https://colab.research.google.com>, **Archivo →
   Abrir cuaderno → GitHub**, pega `CarlosJavierML/Balonmano_ia`, elige la
   rama principal y el cuaderno `deploy/colab/Balonmano_IA.ipynb`.
2. **Entorno de ejecución → Cambiar tipo de entorno de ejecución → GPU T4**.
3. **Entorno de ejecución → Ejecutar todas** y acepta el permiso de Google
   Drive. En 3-5 minutos aparece el **enlace** y la **contraseña**.

Detalles:
- El enlace funciona mientras la pestaña de Colab siga abierta (la versión
  gratuita corta tras unas horas o si no se usa un rato). Para volver, repite
  *Ejecutar todas*: el enlace cambia, pero las sesiones guardadas en Drive
  (carpeta `BalonmanoIA`) siguen ahí.
- Por el enlace se pueden subir vídeos de hasta ~100 MB (límite del túnel
  gratuito de Cloudflare). Para partidos completos, copia los vídeos a
  `BalonmanoIA/importar` en tu Drive y ejecuta el paso 4 del cuaderno.
- **Directo con el móvil**: *Nueva sesión → En directo → Cámara de este
  dispositivo*. El móvil envía su imagen por el enlace y ves las
  estadísticas al momento. Las cámaras IP (RTSP) de la red del pabellón, en
  cambio, no son accesibles desde Google.

## Opción B — Tu ordenador + enlace público (gratis)

Con [Docker Desktop](https://www.docker.com/products/docker-desktop/)
instalado, desde la carpeta del proyecto:

```bash
docker build -t balonmano-ia .
docker run -p 7860:7860 -e BALONMANO_ACCESS_PASSWORD=tu-contraseña \
  -v balonmano-datos:/data balonmano-ia
```

La app queda en <http://localhost:7860>. Para abrirla desde fuera (móvil,
otros entrenadores), en otra terminal, con
[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
instalado:

```bash
cloudflared tunnel --url http://localhost:7860
```

Te da un enlace `https://….trycloudflare.com` (cambia cada vez que lo lanzas).
Si el ordenador está en el pabellón, aquí **sí funciona el directo** con las
cámaras IP de la red local.

## Opción C — Hugging Face Spaces (9 $/mes)

> Desde julio de 2026, crear Spaces de tipo Docker requiere el plan PRO de
> Hugging Face (9 $/mes). A cambio, la app queda siempre disponible en una URL
> fija y se actualiza sola en cada push. El `Dockerfile` construye el
> dashboard, la API y el worker de análisis en un solo contenedor.
>
> El mismo `Dockerfile` sirve para cualquier otro proveedor que ejecute
> contenedores (Render, Railway, Fly.io, un VPS...). Necesita al menos
> ~2 GB de RAM: los planes gratuitos de 512 MB se quedan cortos para YOLO.

### Paso 1 — Crear el Space (5 minutos, una sola vez)

1. Crea una cuenta en <https://huggingface.co/join> y activa el plan PRO.
2. Ve a <https://huggingface.co/new-space>:
   - **Space name**: por ejemplo `balonmano-ia`.
   - **SDK**: `Docker` → plantilla `Blank`.
   - **Hardware**: `CPU basic`.
   - **Visibility**: `Private` si solo lo vas a usar tú, o `Public` para
     compartirlo con el cuerpo técnico (protegido con contraseña, ver paso 2).
3. En el Space: **Settings → Variables and secrets → New secret**:
   - Nombre `BALONMANO_ACCESS_PASSWORD`, valor: la contraseña que quieras.
     **Imprescindible si el Space es público**: la app no tiene cuentas de
     usuario, y sin contraseña cualquiera con el enlace podría subir o borrar
     sesiones. El navegador la pedirá la primera vez (el usuario da igual).
4. Crea un token en <https://huggingface.co/settings/tokens> con permiso
   **Write** y guárdalo para el paso siguiente.

### Paso 2 — Conectar GitHub (una sola vez)

En este repositorio de GitHub: **Settings → Secrets and variables → Actions**:

| Tipo | Nombre | Valor |
|---|---|---|
| Secret | `HF_TOKEN` | el token del paso 1.4 |
| Variable | `HF_SPACE` | `tu-usuario-hf/balonmano-ia` |
| Variable | `DEPLOY_BRANCH` | la rama a desplegar, p. ej. `claude/nifty-edison-8i5k0f` (si no se indica, la rama principal) |

A partir de ahí, **cada push a esa rama despliega automáticamente** (workflow
`.github/workflows/deploy-hf-space.yml`). Para el primer despliegue basta con
hacer un push a la rama, o lanzar el workflow a mano desde la pestaña
**Actions**.

La primera construcción tarda ~10 minutos (instala PyTorch y descarga el
modelo); las siguientes, bastante menos. La app queda en
`https://tu-usuario-hf-balonmano-ia.hf.space`.

#### Alternativa sin GitHub Actions

Puedes subir el código directamente al Space con git: clona el Space, copia
dentro el contenido de este repositorio y añade al principio del `README.md`
la cabecera:

```yaml
---
title: Balonmano IA
sdk: docker
app_port: 7860
---
```

### Qué esperar de un Space con CPU

- **Velocidad**: sin GPU, el análisis va en CPU. Con 2 CPU, cuenta con
  aproximadamente 1–3 minutos de análisis por cada minuto de vídeo (se
  analizan 6 fotogramas por segundo; es una estimación, en la prueba de
  desarrollo 8 s de vídeo tardaron 13 s en una máquina de 4 CPU). Para vídeos largos de partido,
  mejor recortar primero un trozo para las pruebas.
- **Los datos no son permanentes**: el disco normal del Space se borra
  cuando se reinicia (al desplegar una versión nueva, o cuando se "duerme"
  por falta de uso). Para conservarlos, en **Settings →
  Persistent storage** se puede añadir un disco (de pago), que se monta en
  `/data`, justo donde la app guarda todo.
- **Directo**: con la cámara del móvil funciona desde cualquier sitio. Con
  cámaras IP (RTSP), desde la nube solo se puede conectar a cámaras
  accesibles desde internet; una cámara IP en la red del pabellón no es
  visible desde fuera, así que para ellas lo práctico es la opción B.

## Variables de configuración útiles

Todas con prefijo `BALONMANO_`, configurables como secretos/variables del Space:

| Variable | Para qué |
|---|---|
| `BALONMANO_ACCESS_PASSWORD` | Contraseña de acceso (recomendado siempre en la nube) |
| `BALONMANO_ANALYSIS_TARGET_FPS` | Fotogramas analizados por segundo (6 por defecto; bajarlo acelera el análisis) |
| `BALONMANO_WORKER_CONCURRENCY` | Vídeos analizados a la vez (1 por defecto) |
| `BALONMANO_DATABASE_URL` | Para usar PostgreSQL en vez de SQLite |
