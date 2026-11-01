#!/usr/bin/env python3
"""Sensor anti-trampa de RUMBO: falla si los tests se debilitan respecto a la base.

Compara ``tests/`` en la base (``origin/main`` o el commit base del PR) contra el árbol
de trabajo actual y falla si:

1. se elimina cualquier archivo bajo ``tests/``;
2. desaparece alguna función ``def test_*`` (identificada por archivo + nombre calificado);
3. disminuye el número total de ``assert`` en ``tests/``.

La regla solo se puede saltar si el mensaje del último commit (o, si HEAD es un merge de
PR, el del commit de la rama) contiene ``[tests-change-approved]``. Esa marca la pone el
humano, nunca el agente.

Solo usa la biblioteca estándar: se copia tal cual en cada proyecto RUMBO.
Códigos de salida: 0 ok · 1 tests debilitados · 2 error de uso/base no encontrada (--strict).
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MARCA_APROBACION = "[tests-change-approved]"
DIR_TESTS = "tests"
BASES_POR_DEFECTO = ("origin/main", "origin/master", "main", "master")
SHA_NULO = "0" * 40


@dataclass
class Inventario:
    archivos: set[str] = field(default_factory=set)
    tests: set[str] = field(default_factory=set)
    asserts: int = 0
    errores: list[str] = field(default_factory=list)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _qualnames(tree: ast.AST) -> tuple[list[str], int]:
    tests: list[str] = []
    asserts = 0

    def visitar(nodo: ast.AST, prefijo: str) -> None:
        nonlocal asserts
        for hijo in ast.iter_child_nodes(nodo):
            if isinstance(hijo, ast.Assert):
                asserts += 1
            if isinstance(hijo, ast.FunctionDef | ast.AsyncFunctionDef):
                nombre = f"{prefijo}{hijo.name}"
                if hijo.name.startswith("test_"):
                    tests.append(nombre)
                visitar(hijo, nombre + ".")
            elif isinstance(hijo, ast.ClassDef):
                visitar(hijo, f"{prefijo}{hijo.name}.")
            else:
                visitar(hijo, prefijo)

    visitar(tree, "")
    return tests, asserts


def analizar(archivos: dict[str, str | None]) -> Inventario:
    """``archivos``: ruta → contenido (``None`` si no es un .py legible)."""
    inv = Inventario()
    for ruta, contenido in archivos.items():
        inv.archivos.add(ruta)
        if contenido is None or not ruta.endswith(".py"):
            continue
        try:
            tree = ast.parse(contenido, filename=ruta)
        except SyntaxError as exc:
            inv.errores.append(f"{ruta}: no se puede parsear ({exc.msg}, línea {exc.lineno})")
            continue
        tests, asserts = _qualnames(tree)
        inv.tests.update(f"{ruta}::{t}" for t in tests)
        inv.asserts += asserts
    return inv


def inventario_base(base: str, raiz: Path) -> Inventario:
    ls = _git(["ls-tree", "-r", "--name-only", base, "--", DIR_TESTS], raiz)
    archivos: dict[str, str | None] = {}
    for ruta in filter(None, ls.stdout.splitlines()):
        contenido = None
        if ruta.endswith(".py"):
            show = _git(["show", f"{base}:{ruta}"], raiz)
            contenido = show.stdout if show.returncode == 0 else None
        archivos[ruta] = contenido
    return analizar(archivos)


def inventario_actual(raiz: Path) -> Inventario:
    archivos: dict[str, str | None] = {}
    carpeta = raiz / DIR_TESTS
    if carpeta.is_dir():
        for p in sorted(carpeta.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                rel = p.relative_to(raiz).as_posix()
                archivos[rel] = (
                    p.read_text(encoding="utf-8", errors="replace") if (p.suffix == ".py") else None
                )
    return analizar(archivos)


def comparar(base: Inventario, actual: Inventario) -> list[str]:
    problemas: list[str] = []
    for ruta in sorted(base.archivos - actual.archivos):
        problemas.append(f"archivo de tests eliminado: {ruta}")
    for test in sorted(base.tests - actual.tests):
        problemas.append(f"test eliminado o renombrado: {test}")
    if actual.asserts < base.asserts:
        problemas.append(f"asserts en tests/ bajaron de {base.asserts} a {actual.asserts}")
    problemas.extend(f"error de parseo: {e}" for e in actual.errores)
    return problemas


def resolver_base(explicita: str | None, raiz: Path) -> str | None:
    candidatas: list[str] = []
    for valor in (explicita, os.environ.get("RUMBO_BASE_REF")):
        if valor and valor.strip() and valor.strip() != SHA_NULO:
            candidatas.append(valor.strip())
    gh_base = os.environ.get("GITHUB_BASE_REF")
    if gh_base:
        candidatas.append(f"origin/{gh_base}")
    candidatas.extend(BASES_POR_DEFECTO)
    for cand in candidatas:
        rp = _git(["rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"], raiz)
        if rp.returncode == 0:
            mb = _git(["merge-base", "HEAD", cand], raiz)
            return mb.stdout.strip() if mb.returncode == 0 and mb.stdout.strip() else cand
    return None


def mensaje_aprobacion(raiz: Path) -> str:
    """Mensaje del último commit; si HEAD es un merge (PR en CI), el del commit de la rama."""
    padres = _git(["rev-list", "--parents", "-n", "1", "HEAD"], raiz).stdout.split()
    ref = "HEAD^2" if len(padres) > 2 else "HEAD"
    return _git(["log", "-1", "--format=%B", ref], raiz).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "Sensor anti-trampa").splitlines()[0])
    parser.add_argument("--base", help="ref o SHA base (por defecto: origin/main o base del PR)")
    parser.add_argument("--root", default=".", help="raíz del repositorio")
    parser.add_argument(
        "--strict", action="store_true", help="falla (código 2) si no se encuentra la base"
    )
    args = parser.parse_args(argv)
    raiz = Path(args.root).resolve()

    base = resolver_base(args.base, raiz)
    if base is None:
        print("AVISO: no encontré una base para comparar (¿sin origin/main?).")
        return 2 if args.strict else 0

    problemas = comparar(inventario_base(base, raiz), inventario_actual(raiz))
    if not problemas:
        print(f"OK: ningún test debilitado respecto a {base[:12]}.")
        return 0

    print(f"Tests debilitados respecto a {base[:12]}:")
    for p in problemas:
        print(f"  - {p}")
    if MARCA_APROBACION in mensaje_aprobacion(raiz):
        print(f"Aprobado por el humano con {MARCA_APROBACION} en el mensaje del commit.")
        return 0
    print(
        "Si el cambio es legítimo, el HUMANO debe aprobarlo añadiendo "
        f"{MARCA_APROBACION} al mensaje del último commit. El agente nunca pone esa marca."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
