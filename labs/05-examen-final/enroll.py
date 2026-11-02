"""Enrolamiento LOCAL de un integrante (nunca desde el chat).

    uv run python labs/05-examen-final/enroll.py --usuario integrante_1 --telegram-id 123456 \
        fotos/integrante_1/a.jpg fotos/integrante_1/b.jpg fotos/integrante_1/c.jpg [...hasta 5]

  - Rechaza fotos sin rostro o con más de uno.
  - Guarda SOLO los embeddings en rostros/<usuario>.npy (matriz n×128). No copia las fotos.
  - Asocia el telegram_user_id al usuario en data/usuarios.json (factor de posesión).

¿Por qué no se enrola desde Telegram? Quien tenga acceso a la cuenta (un celular prestado o
robado, una sesión abierta) podría registrar SU rostro y suplantar al dueño; además las
fotos pasarían por los servidores de Telegram. El enrolamiento es un acto presencial y de
confianza: lo hace el equipo, en local, con el consentimiento de la persona.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from config import ROSTROS_DIR, USUARIOS_FILE
from face import MotorFacial, RostroError, ruta_embeddings
from usuarios import actualizar_telegram_id, buscar_por_id, cargar_usuarios

MIN_FOTOS, MAX_FOTOS = 3, 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrola un usuario con 3 a 5 fotos (solo guarda embeddings).")
    parser.add_argument("--usuario", required=True, help="id en data/usuarios.json (integrante_1 o integrante_2)")
    parser.add_argument("--telegram-id", type=int, required=True, help="telegram_user_id (se obtiene con /quiensoy)")
    parser.add_argument("fotos", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if buscar_por_id(cargar_usuarios(USUARIOS_FILE), args.usuario) is None:
        print(f"[error] No existe el usuario {args.usuario!r} en {USUARIOS_FILE.name}")
        return 2
    if not MIN_FOTOS <= len(args.fotos) <= MAX_FOTOS:
        print(f"[error] Se requieren entre {MIN_FOTOS} y {MAX_FOTOS} fotos (recibidas: {len(args.fotos)}).")
        return 2

    motor = MotorFacial()
    embeddings = []
    for foto in args.fotos:
        try:
            embeddings.append(motor.embedding_de_archivo(foto))
            print(f"  ok        {foto.name}")
        except (RostroError, ValueError) as exc:
            print(f"  RECHAZADA {foto.name}: {exc}")

    if len(embeddings) < MIN_FOTOS:
        print(f"[error] Solo {len(embeddings)} fotos válidas; se necesitan al menos {MIN_FOTOS}. No se guardó nada.")
        return 1

    ROSTROS_DIR.mkdir(parents=True, exist_ok=True)
    destino = ruta_embeddings(ROSTROS_DIR, args.usuario)
    np.save(destino, np.vstack(embeddings).astype(np.float32))
    actualizar_telegram_id(USUARIOS_FILE, args.usuario, args.telegram_id)
    print(f"\nGuardados {len(embeddings)} embeddings (128 dims) en {destino}")
    print(f"telegram_user_id {args.telegram_id} asociado a {args.usuario} en {USUARIOS_FILE.name}")
    print("Las fotos NO se copiaron. Bórralas al terminar las pruebas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
