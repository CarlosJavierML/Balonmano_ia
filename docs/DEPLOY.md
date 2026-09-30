# Despliegue en la nube

La forma más sencilla de probar la app desde cualquier sitio (móvil,
ordenador del club...) es **Hugging Face Spaces**: es gratis, da 16 GB de
RAM y 2 CPU (suficiente para analizar vídeos con YOLO) y construye
directamente el `Dockerfile` de la raíz del repositorio, que empaqueta el
dashboard, la API y el worker de análisis en un solo contenedor y una sola
URL.

> El mismo `Dockerfile` sirve para cualquier otro proveedor que ejecute
> contenedores (Render, Railway, Fly.io, un VPS...). Necesita al menos
> ~2 GB de RAM: los planes gratuitos de 512 MB se quedan cortos para YOLO.

## Paso 1 — Crear el Space (5 minutos, una sola vez)

1. Crea una cuenta en <https://huggingface.co/join> (gratis).
2. Ve a <https://huggingface.co/new-space>:
   - **Space name**: por ejemplo `balonmano-ia`.
   - **SDK**: `Docker` → plantilla `Blank`.
   - **Hardware**: `CPU basic · Free`.
   - **Visibility**: `Private` si solo lo vas a usar tú, o `Public` para
     compartirlo con el cuerpo técnico (protegido con contraseña, ver paso 2).
3. En el Space: **Settings → Variables and secrets → New secret**:
   - Nombre `BALONMANO_ACCESS_PASSWORD`, valor: la contraseña que quieras.
     **Imprescindible si el Space es público**: la app no tiene cuentas de
     usuario, y sin contraseña cualquiera con el enlace podría subir o borrar
     sesiones. El navegador la pedirá la primera vez (el usuario da igual).
4. Crea un token en <https://huggingface.co/settings/tokens> con permiso
   **Write** y guárdalo para el paso siguiente.

## Paso 2 — Conectar GitHub (una sola vez)

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

### Alternativa sin GitHub Actions

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

## Qué esperar del plan gratuito

- **Velocidad**: sin GPU, el análisis va en CPU. Con 2 CPU, cuenta con
  aproximadamente 1–3 minutos de análisis por cada minuto de vídeo (se
  analizan 6 fotogramas por segundo; es una estimación, en la prueba de
  desarrollo 8 s de vídeo tardaron 13 s en una máquina de 4 CPU). Para vídeos largos de partido,
  mejor recortar primero un trozo para las pruebas.
- **Los datos no son permanentes**: el disco del plan gratuito se borra
  cuando el Space se reinicia (al desplegar una versión nueva, o tras 48 h
  sin uso, cuando se "duerme"). Para conservarlos, en **Settings →
  Persistent storage** se puede añadir un disco (de pago), que se monta en
  `/data`, justo donde la app guarda todo.
- **Directo (RTSP)**: desde la nube solo se puede conectar a cámaras
  accesibles desde internet. Una cámara IP en la red del pabellón no es
  visible desde fuera; para el directo, lo práctico es ejecutar la app en un
  ordenador del propio pabellón (`docker compose up`).

## Variables de configuración útiles

Todas con prefijo `BALONMANO_`, configurables como secretos/variables del Space:

| Variable | Para qué |
|---|---|
| `BALONMANO_ACCESS_PASSWORD` | Contraseña de acceso (recomendado siempre en la nube) |
| `BALONMANO_ANALYSIS_TARGET_FPS` | Fotogramas analizados por segundo (6 por defecto; bajarlo acelera el análisis) |
| `BALONMANO_WORKER_CONCURRENCY` | Vídeos analizados a la vez (1 por defecto) |
| `BALONMANO_DATABASE_URL` | Para usar PostgreSQL en vez de SQLite |
