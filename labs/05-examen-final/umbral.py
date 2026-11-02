"""Tabla FAR/FRR para elegir el umbral de similitud facial (R2).

    uv run python labs/05-examen-final/umbral.py \
        --persona fotos/integrante_1 --persona fotos/integrante_2 \
        --impostores fotos/impostores_lfw [--umbrales 0.25 0.30 0.363 0.40 0.45]

  - Pares GENUINOS: todas las parejas de fotos de una misma persona (5 fotos → C(5,2)=10).
  - Pares IMPOSTORES: integrante vs. el otro integrante, y cada integrante vs. cada foto de
    impostores (dataset público LFW: cada foto es una persona distinta).
  - FAR = impostores aceptados / total de impostores   (aceptar a quien no es: error GRAVE)
    FRR = genuinos rechazados / total de genuinos       (rechazar al dueño: molesto, reintenta)

Guarda la tabla en resultados/umbral.md para el informe (solo números, nada biométrico).
"""

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np

from config import LAB_DIR
from face import MotorFacial, RostroError, similitud

UMBRALES = [0.25, 0.30, 0.363, 0.40, 0.45, 0.50]
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp"}
SALIDA = LAB_DIR / "resultados" / "umbral.md"


def fotos_de(carpeta: Path) -> list[Path]:
    return sorted(p for p in carpeta.rglob("*") if p.suffix.lower() in EXTENSIONES)


def embeddings_de(motor: MotorFacial, carpeta: Path) -> list[np.ndarray]:
    resultado = []
    for foto in fotos_de(carpeta):
        try:
            resultado.append(motor.embedding_de_archivo(foto))
        except (RostroError, ValueError) as exc:
            print(f"  (descartada {foto.name}: {exc})")
    return resultado


def tasas(genuinos: list[float], impostores: list[float], umbral: float) -> tuple[float, float, int, int]:
    aceptados = sum(s >= umbral for s in impostores)
    rechazados = sum(s < umbral for s in genuinos)
    far = aceptados / len(impostores) if impostores else 0.0
    frr = rechazados / len(genuinos) if genuinos else 0.0
    return far, frr, aceptados, rechazados


def _resumen(nombre: str, valores: list[float]) -> str:
    if not valores:
        return f"Similitud {nombre}: (sin pares)"
    return f"Similitud {nombre}: min {min(valores):.3f} · media {np.mean(valores):.3f} · max {max(valores):.3f}"


def tabla_markdown(genuinos: list[float], impostores: list[float], umbrales: list[float]) -> str:
    lineas = [
        f"Pares genuinos: **{len(genuinos)}** · pares impostores: **{len(impostores)}**",
        "",
        _resumen("genuinos", genuinos),
        "",
        _resumen("impostores", impostores),
        "",
        "| Umbral | FAR | FRR | Impostores aceptados | Genuinos rechazados |",
        "|---:|---:|---:|---:|---:|",
    ]
    for u in umbrales:
        far, frr, acep, rech = tasas(genuinos, impostores, u)
        lineas.append(f"| {u:.3f} | {far:.1%} | {frr:.1%} | {acep}/{len(impostores)} | {rech}/{len(genuinos)} |")
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calcula FAR/FRR para varios umbrales.")
    parser.add_argument("--persona", action="append", type=Path, default=[], help="carpeta con fotos de un integrante")
    parser.add_argument("--impostores", type=Path, help="carpeta con fotos de terceros (dataset público)")
    parser.add_argument("--umbrales", nargs="+", type=float, default=UMBRALES)
    args = parser.parse_args(argv)
    if not args.persona:
        parser.error("indica al menos una --persona")

    motor = MotorFacial()
    personas = {}
    for carpeta in args.persona:
        print(f"Embeddings de {carpeta.name} ...")
        personas[carpeta.name] = embeddings_de(motor, carpeta)
    terceros = []
    if args.impostores:
        print(f"Embeddings de {args.impostores.name} ...")
        terceros = embeddings_de(motor, args.impostores)

    genuinos = [similitud(a, b) for embs in personas.values() for a, b in itertools.combinations(embs, 2)]
    impostores = [
        similitud(a, b)
        for (_, embs_a), (_, embs_b) in itertools.combinations(personas.items(), 2)
        for a in embs_a
        for b in embs_b
    ]
    impostores += [similitud(a, t) for embs in personas.values() for a in embs for t in terceros]

    if len(genuinos) < 10 or len(impostores) < 20:
        print(f"\n[aviso] El enunciado exige ≥10 genuinos y ≥20 impostores (hay {len(genuinos)} y {len(impostores)}).")

    tabla = tabla_markdown(genuinos, impostores, sorted(args.umbrales))
    print("\n" + tabla)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text("# Tabla FAR/FRR\n\n" + tabla + "\n", encoding="utf-8")
    print(f"\nGuardado en {SALIDA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
