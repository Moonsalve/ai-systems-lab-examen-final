"""Consola del núcleo (sin Telegram): sirve para probar el control de acceso y ver la traza.

La sesión se simula desde usuarios.json con --rol (no hay rostro en consola). Es el mismo
núcleo que usa bot.py: si algo funciona aquí, funciona en Telegram.

Uso:
    uv run python labs/05-examen-final/chatbot.py [--rol publico|estudiante|docente]
                                                 [--modo agente|router] [--debug]
Comandos: /rol publico|estudiante|docente  /modo agente|router  /reiniciar  /salir
"""

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from typing import Any

from auditoria import Auditoria
from config import AUDIT_FILE
from llm_client import Message
from nucleo import Nucleo, create_nucleo
from pruebas_seguridad import sesion_para
from tools import Tool

ROLES = ("publico", "estudiante", "docente")
MODES = ("agente", "router")


async def ask(prompt: str) -> str:
    return (await asyncio.to_thread(input, prompt)).strip()


async def confirm_in_console(tool: Tool, arguments: dict[str, Any]) -> bool:
    print(f"\n[confirmación] El asistente quiere ejecutar una ACCIÓN de {tool.origin}:")
    print(f"  {tool.name}({json.dumps(arguments, ensure_ascii=False)})")
    return (await ask("¿La autorizas? (s/n): ")).lower() in {"s", "si", "sí"}


def opcion(argv: list[str], nombre: str, opciones: tuple[str, ...], defecto: str) -> str:
    if nombre in argv:
        i = argv.index(nombre) + 1
        if i < len(argv) and argv[i] in opciones:
            return argv[i]
        raise ValueError(f"{nombre} debe ser uno de: {', '.join(opciones)}")
    return defecto


async def chat(nucleo: Nucleo, rol: str, mode: str, debug: bool) -> None:
    history: list[Message] = []
    while True:
        try:
            user_input = await ask(f"Tú [{rol} · {mode}]: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input == "/salir":
            break
        if user_input == "/reiniciar":
            history.clear()
            print("(historial borrado)\n")
            continue
        if user_input.startswith("/rol"):
            nuevo = user_input.removeprefix("/rol").strip()
            if nuevo in ROLES:
                rol = nuevo
                history.clear()
                print(f"(sesión simulada: {rol}; historial borrado)\n")
            else:
                print(f"Uso: /rol {' | '.join(ROLES)}\n")
            continue
        if user_input.startswith("/modo"):
            nuevo = user_input.removeprefix("/modo").strip()
            if nuevo in MODES:
                mode = nuevo
                print(f"(modo {mode})\n")
            continue

        respuesta = await nucleo.responder(user_input, history, sesion_para(rol), confirm_in_console, mode)
        if debug:
            print("\n--- Traza ---\n" + respuesta.traza() + "\n-------------")
        print(f"\nAsistente: {respuesta.texto}\n")
        if not respuesta.error:
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": respuesta.texto})


async def main() -> None:
    debug = "--debug" in sys.argv
    try:
        rol = opcion(sys.argv, "--rol", ROLES, "publico")
        mode = opcion(sys.argv, "--modo", MODES, "agente")
    except ValueError as exc:
        print(f"[uso] {exc}")
        return

    async with AsyncExitStack() as stack:
        try:
            nucleo = await create_nucleo(stack, Auditoria(AUDIT_FILE), mode=mode)
        except (ValueError, FileNotFoundError) as exc:
            print(f"[configuración] {exc}")
            return
        settings = nucleo.orchestrator.llm.settings
        print(f"Asistente del Curso de IA · núcleo sin Telegram  ({settings.provider} · {settings.model})")
        print(f"Índice: {len(nucleo.orchestrator.rag.store)} fragmentos {nucleo.orchestrator.rag.store.niveles()}")
        print("Comandos: /rol publico|estudiante|docente  /modo agente|router  /reiniciar  /salir\n")
        await chat(nucleo, rol, mode, debug)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
