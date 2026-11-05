# Examen final — Asistente del Curso en Telegram con reconocimiento facial

`Usuario (Telegram) → Identidad (rostro + prueba de vida) → Orquestador → LLM + RAG + Tools + MCP`

Grupo: Daniel Felipe Aguilar Latorre (integrante 1, estudiante) · Juan David Monsalve Niño (integrante 2, docente).

Todos los comandos se ejecutan **desde la raíz del repositorio**. En Windows conviene
`$env:PYTHONUTF8 = "1"` (PowerShell) para que la consola muestre tildes y flechas.

## 1. Preparación (una vez)

```bash
uv sync                                   # dependencias (incluye python-telegram-bot y opencv-python)
cp .env.example .env                      # PowerShell: Copy-Item .env.example .env
```

En `.env` completar: `LLM_API_KEY` (Groq), `TELEGRAM_BOT_TOKEN` (de @BotFather) y, después de
medir FAR/FRR, `FACE_MATCH_THRESHOLD`.

Modelos de rostro (no se versionan) en `labs/05-examen-final/modelos/`:

```bash
mkdir -p labs/05-examen-final/modelos && cd labs/05-examen-final/modelos
curl -L -O https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
curl -L -O https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
cd ../../..
```

Índice RAG con niveles (rehacer si cambian los documentos):

```bash
uv run python labs/05-examen-final/ingest.py
```

## 2. Identidad

1. Arrancar el bot (sección 3) y que cada integrante envíe `/quiensoy` para ver su `telegram_user_id`.
2. Cada integrante toma 5 fotos de frente (buena luz, un solo rostro) en `labs/05-examen-final/fotos/integrante_N/`.
3. Enrolar (solo guarda embeddings en `rostros/`, nunca las fotos):

```bash
uv run python labs/05-examen-final/enroll.py --usuario integrante_1 --telegram-id <ID_DANIEL> labs/05-examen-final/fotos/integrante_1/*.jpg
uv run python labs/05-examen-final/enroll.py --usuario integrante_2 --telegram-id <ID_JUAN> labs/05-examen-final/fotos/integrante_2/*.jpg
```

4. Tabla FAR/FRR (20 pares genuinos, ≥200 impostores con LFW) → `resultados/umbral.md`:

```bash
uv run python labs/05-examen-final/umbral.py --persona labs/05-examen-final/fotos/integrante_1 \
  --persona labs/05-examen-final/fotos/integrante_2 --impostores labs/05-examen-final/fotos/impostores_lfw
```

5. Calibrar el signo del giro con una video-nota descargada (Telegram Desktop → guardar como):

```bash
uv run python labs/05-examen-final/liveness.py --calibrar ruta/al/video.mp4
```

Si al girar a TU izquierda el yaw crudo se vuelve negativo, poner `"signo_camara": -1` a ese usuario en
`data/usuarios.json`: depende del celular de cada uno. `LIVENESS_SIGNO_IZQUIERDA` del `.env` es solo el valor por defecto.

## 3. Ejecutar

```bash
uv run python labs/05-examen-final/bot.py                 # Telegram (long polling)
uv run python labs/05-examen-final/chatbot.py --rol estudiante --debug   # núcleo en consola, sin Telegram
uv run python labs/05-examen-final/pruebas_seguridad.py  # suite de ataques (R8) → resultados/pruebas_seguridad.md
uv run pytest labs/05-examen-final/tests -q              # pruebas unitarias/integración (sin API key)
```

Comandos del bot: `/start`, `/quiensoy`, `/login`, `/logout`, `/traza`, `/reiniciar`, `/ayuda`.

## 4. Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `bot.py` | Adaptador Telegram: comandos, video-notas, botones de confirmación. Sin lógica de negocio. |
| `nucleo.py` | `Nucleo.responder(pregunta, historial, sesion, confirm)`: punto de entrada sin Telegram. |
| `orchestrator.py` | Arma el agente **por pregunta** con las capacidades del rol (modo agente o router). |
| `agent.py` | Ciclo del agente; `execute` re-verifica el rol (defensa en profundidad) y audita. |
| `acceso.py` | Política de acceso: niveles y tools por rol. Única fuente de verdad. |
| `sesion.py` / `sesiones.py` | Sesión (rol, nombre, código desde `usuarios.json`), retos, expiración, bloqueo. |
| `rag.py` / `vector_store.py` / `documents.py` / `capabilities.py` | RAG con nivel por fragmento y filtro **antes** del top-k. |
| `mcp_server.py` / `mcp_client.py` | Tools MCP por rol; identidad inyectada en `_meta` (el LLM no la ve ni la controla). |
| `face.py` / `enroll.py` / `umbral.py` | YuNet + SFace, enrolamiento local, tabla FAR/FRR. |
| `liveness.py` / `login.py` | Prueba de vida sobre la video-nota y flujo de login 1:1. |
| `auditoria.py` | `auditoria.jsonl` sin imágenes ni embeddings. |
| `fundamentacion.py` | Descarta respuestas con fechas sin fuente cuando el tema es restringido. |
| `pruebas_seguridad.py` | Casos A1–A8 + P1–P14 sobre el núcleo. |

## 5. Lo que NUNCA se sube

`.env`, `modelos/`, `rostros/`, `fotos/`, `index/`, `auditoria*.jsonl`, videos. Ya están en `.gitignore`.
Al terminar: borrar `rostros/` y `fotos/` (declaración en el informe).
