# Pendientes — parte de Telegram (para Juan David)

El código del bot ya está escrito y probado sin conexión (`labs/05-examen-final/bot.py`). Lo que falta es **conectarlo a Telegram y probarlo con el celular**. El núcleo, la seguridad y la suite de ataques ya funcionan (21/21).

## 0. Primero: instalar el entorno (una sola vez)

La carpeta se envió sin el entorno de Python (`.venv`) para que pesara menos. Necesitas [uv](https://docs.astral.sh/uv/) instalado; en PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`. Luego, desde la carpeta `ai-systems-lab-students`:

```powershell
uv sync
```

Eso instala todo (Python 3.11+ y las librerías). La primera vez que se use el RAG, también se descarga el modelo de embeddings (~220 MB).

## 1. Crear el bot y conseguir el token (5 minutos)

1. En Telegram (celular o Desktop), buscar **@BotFather** (tiene el chulo azul de verificado).
2. Enviarle `/newbot`.
3. Nombre visible: por ejemplo `Asistente Curso IA - Daniel y Juan David`.
4. Usuario del bot (debe terminar en `bot`): por ejemplo `asistente_ia_dfal_jdmn_bot`.
5. BotFather responde con un **token** parecido a `1234567890:AAH...`. **Es un secreto**: no lo pegues en el chat del grupo, en el informe ni en el repositorio.
6. Abrir `ai-systems-lab-students/.env` (ya existe, tiene la API key de Groq) y pegarlo en la línea `TELEGRAM_BOT_TOKEN=`.

## 2. Arrancar el bot

Desde la carpeta `ai-systems-lab-students` (PowerShell):

```powershell
$env:PYTHONUTF8 = "1"
uv run python labs/05-examen-final/bot.py
```

Debe aparecer `Bot en línea (modo agente, umbral 0.363, sesión 10 min)`. Se detiene con Ctrl+C. Mientras esté corriendo, el bot responde (long polling: no necesita URL pública).

## 3. Probar sin login (pruebas 1, 2 y 3)

En el chat con el bot: `/start`, "¿Cuándo es el segundo parcial?", "¿Cuándo es el simulacro del segundo parcial?" y `/traza`.

## 4. Datos de los dos (necesarios para el login)

1. Cada uno envía `/quiensoy` al bot y anota su `telegram_user_id`.
2. Fotos para enrolar: **5 por persona**, de frente, buena luz, un solo rostro, en
   - `labs/05-examen-final/fotos/integrante_1/` (Daniel; ya hay 1 foto suya en la carpeta del examen, faltan 4)
   - `labs/05-examen-final/fotos/integrante_2/` (Juan David)
3. Enrolar (ver `labs/05-examen-final/README.md`, sección 2) y generar la tabla FAR/FRR con `umbral.py`.

## 5. Calibrar el giro de la video-nota

1. `/login` en el bot y enviar una video-nota girando a **tu izquierda**.
2. En Telegram Desktop, clic derecho en la video-nota → guardar como `.mp4`.
3. `uv run python labs/05-examen-final/liveness.py --calibrar ruta\al\video.mp4`
4. Si al girar a tu izquierda el `yaw` se vuelve **negativo**, agregar `LIVENESS_SIGNO_IZQUIERDA=-1` al `.env` y reiniciar el bot.

## 6. Antes de grabar

- Seguir `GUIA_VIDEO.md` (guion, tiempos y checklist de las 15 pruebas).
- Para las pruebas 13 y 14 (bloqueo y expiración), bajar temporalmente `SESSION_MINUTES=1` en el `.env`.
- Después de la prueba 9 (registrar nota), restaurar los datos: copiar `data/academico.backup.json` sobre `data/academico.json`.
- Al terminar: copiar al informe 3 líneas de `labs/05-examen-final/auditoria.jsonl` (un login aceptado, uno rechazado y un acceso denegado), y luego borrar `fotos/`, `rostros/` y `auditoria.jsonl`.

Si algo falla, el log de la consola del bot muestra el error completo, y `/traza` en el chat muestra qué tools y documentos se usaron.
