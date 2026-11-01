"""CLI de RUMBO.

rumbo init                 monta el arnés (Parte A) en el directorio actual
rumbo new adr|spec|postmortem "Título"
rumbo decision "texto"     anota una decisión humana en docs/decisiones.md
rumbo gate G1..G5          chequeos automáticos + checklist humano del gate
rumbo doctor               verifica que la estructura del arnés esté completa
rumbo check-tests          sensor anti-trampa (tests no debilitados)
rumbo verify-dep NOMBRE    verifica un paquete en PyPI antes de añadirlo
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from rumbo import __version__
from rumbo.gates import GATES, evaluar, informe
from rumbo.proyecto import Proyecto, slug
from rumbo.scaffold import PROVEEDORES, RIGORES, Config, instalar, pkg_valido, scaffold
from rumbo.sensors import check_tests_not_weakened


# ------------------------------------------------------------------------- init
def _preguntar(texto: str, defecto: str = "") -> str:
    sufijo = f" [{defecto}]" if defecto else ""
    resp = input(f"{texto}{sufijo}: ").strip()
    return resp or defecto


def _a_snake(nombre: str) -> str:
    return slug(nombre).replace("-", "_") or "app"


def _config_init(args: argparse.Namespace) -> Config:
    interactivo = sys.stdin.isatty() and not args.yes
    nombre = args.nombre
    pkg = args.pkg
    llm = args.llm
    proveedor = args.proveedor
    rigor = args.rigor
    caso = args.caso

    if interactivo:
        print("A0 · Preguntas iniciales de RUMBO (Enter acepta el valor entre corchetes)\n")
        nombre = nombre or _preguntar("1. Nombre del proyecto", Path(args.dir).resolve().name)
        pkg = pkg or _preguntar("   Paquete Python (snake_case)", _a_snake(nombre))
        if llm is None:
            llm = _preguntar("2. ¿Es un producto con LLM? (si/no)", "no").lower() in (
                "si",
                "sí",
                "s",
                "yes",
                "y",
            )
        if llm and not proveedor:
            opciones = "/".join([*PROVEEDORES, "otro"])
            proveedor = _preguntar(f"   Proveedor del modelo ({opciones})", "anthropic")
        if not rigor:
            rigor = _preguntar("3. Nivel de rigor (prototipo/mvp/produccion)", "mvp")
        if caso is None:
            caso = _preguntar("4. ¿Hay un caso concreto? (descríbelo o deja vacío)", "")
    nombre = nombre or Path(args.dir).resolve().name
    pkg = pkg or _a_snake(nombre)
    llm = bool(llm)
    if llm and not proveedor:
        raise SystemExit("Con --llm debes indicar --proveedor (o usar el modo interactivo).")
    if llm and proveedor not in PROVEEDORES and not (args.sdk_paquete and args.sdk_modulo):
        print(
            f"AVISO: proveedor {proveedor!r} sin SDK conocido. Pasa --sdk-paquete y --sdk-modulo "
            "para añadirlo y prohibirlo en domain/; verifica antes el paquete con "
            "'rumbo verify-dep'."
        )
    return Config(
        nombre=nombre,
        pkg=pkg,
        llm=llm,
        proveedor=proveedor or "",
        rigor=rigor or "mvp",
        caso=caso or "",
        sdk_paquete=args.sdk_paquete or "",
        sdk_modulo=args.sdk_modulo or "",
    )


def cmd_init(args: argparse.Namespace) -> int:
    try:
        cfg = _config_init(args)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2
    destino = Path(args.dir).resolve()
    print(f"\nMontando el arnés RUMBO en {destino}")
    print(f"  paquete={cfg.pkg} llm={cfg.llm} proveedor={cfg.proveedor or '-'} rigor={cfg.rigor}\n")
    reporte = scaffold(cfg, destino, forzar=args.force)
    if not args.no_install:
        instalar(cfg, destino, reporte)
    print("\n" + reporte.resumen())
    print(
        "\nSiguientes pasos (A8):\n"
        "  1. make check            # debe quedar en verde\n"
        "  2. git add -A && git commit -m 'chore: arnés RUMBO inicial'\n"
        '  3. Abre Claude Code y escribe: "Lee AGENTS.md y RUMBO.md; empecemos la fase R".'
    )
    return 1 if any(rc != 0 for _, rc in reporte.comandos) else 0


# -------------------------------------------------------------------------- new
_NUEVOS = {
    "adr": ("docs/adr", "docs/adr/0000-plantilla.md"),
    "spec": ("specs", "specs/0000-plantilla.md"),
    "postmortem": ("docs/postmortems", "docs/postmortems/0000-plantilla.md"),
}


def _siguiente_numero(carpeta: Path) -> int:
    nums = [int(p.name[:4]) for p in carpeta.glob("[0-9][0-9][0-9][0-9]-*.md")]
    return max(nums, default=0) + 1


def cmd_new(args: argparse.Namespace) -> int:
    raiz = Path(args.dir).resolve()
    carpeta_rel, plantilla_rel = _NUEVOS[args.tipo]
    plantilla = raiz / plantilla_rel
    if not plantilla.exists():
        print(f"Error: falta la plantilla {plantilla_rel}; ¿ejecutaste 'rumbo init'?")
        return 2
    texto = plantilla.read_text(encoding="utf-8")
    hoy = dt.date.today().isoformat()
    carpeta = raiz / carpeta_rel
    if args.tipo == "postmortem":
        destino = carpeta / f"{hoy}-{slug(args.titulo)}.md"
        texto = re.sub(
            r"^# .*$", f"# Postmortem: {args.titulo} ({hoy})", texto, count=1, flags=re.M
        )
    else:
        n = _siguiente_numero(carpeta)
        destino = carpeta / f"{n:04d}-{slug(args.titulo)}.md"
        prefijo = "ADR" if args.tipo == "adr" else "F"
        texto = re.sub(r"^# .*$", f"# {prefijo}-{n:04d}: {args.titulo}", texto, count=1, flags=re.M)
        texto = texto.replace("Fecha: AAAA-MM-DD", f"Fecha: {hoy}")
    if destino.exists():
        print(f"Error: ya existe {destino.relative_to(raiz)}")
        return 2
    destino.write_text(texto, encoding="utf-8")
    print(f"Creado {destino.relative_to(raiz).as_posix()}")
    return 0


# --------------------------------------------------------------------- decision
def cmd_decision(args: argparse.Namespace) -> int:
    archivo = Path(args.dir).resolve() / "docs" / "decisiones.md"
    if not archivo.exists():
        print("Error: falta docs/decisiones.md; ¿ejecutaste 'rumbo init'?")
        return 2
    hoy = dt.date.today().isoformat()
    entrada = f"\n## {hoy} — {args.titulo}\n- Decisión: {args.titulo}\n"
    if args.porque:
        entrada += f"- Por qué: {args.porque}\n"
    entrada += f"- Decidió: {args.quien}\n"
    with archivo.open("a", encoding="utf-8") as f:
        f.write(entrada)
    print(f"Anotado en docs/decisiones.md ({hoy}).")
    return 0


# ------------------------------------------------------------------------- gate
def cmd_gate(args: argparse.Namespace) -> int:
    try:
        proyecto = Proyecto.cargar(Path(args.dir).resolve())
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        return 2
    gate, resultados = evaluar(args.codigo, proyecto, ejecutar=not args.sin_ejecutar)
    texto, ok = informe(gate, resultados)
    print(texto)
    return 0 if ok else 1


# ----------------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace) -> int:
    raiz = Path(args.dir).resolve()
    try:
        p = Proyecto.cargar(raiz)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        return 2
    esperados = [
        "AGENTS.md",
        "CLAUDE.md",
        "RUMBO.md",
        "Makefile",
        "pyproject.toml",
        ".pre-commit-config.yaml",
        ".gitignore",
        ".github/workflows/ci.yml",
        "docs/product/brief.md",
        "docs/product/prd.md",
        "docs/adr/0000-plantilla.md",
        "docs/architecture/c4.md",
        "docs/security/threat-model.md",
        "docs/ops/runbook.md",
        "docs/ops/slos.md",
        "docs/decisiones.md",
        "specs/0000-plantilla.md",
        f"src/{p.pkg}/domain/__init__.py",
        f"src/{p.pkg}/app/__init__.py",
        f"src/{p.pkg}/adapters/__init__.py",
        f"src/{p.pkg}/api/__init__.py",
        "tests/unit",
        "tests/property",
        "tests/integration",
        "tests/architecture",
        "scripts/check_tests_not_weakened.py",
        "spikes/README.md",
        "infra/Dockerfile",
    ]
    if p.llm:
        esperados += [
            f"src/{p.pkg}/prompts/__init__.py",
            "evals/run_evals.py",
            "evals/datasets/smoke.jsonl",
            "evals/graders/__init__.py",
        ]
    if (raiz / "uv.lock").exists() is False:
        print("AVISO: falta uv.lock (CI usa 'uv sync --locked'). Ejecuta 'uv lock'.")
    faltan = [e for e in esperados if not (raiz / e).exists()]
    agents = (
        (raiz / "AGENTS.md").read_text(encoding="utf-8") if (raiz / "AGENTS.md").exists() else ""
    )
    n_lineas = len(agents.splitlines())
    for f in faltan:
        print(f"❌ falta {f}")
    if n_lineas > 100:
        print(f"❌ AGENTS.md tiene {n_lineas} líneas (>100): es un mapa, no un manual")
    if not faltan and n_lineas <= 100:
        print(f"✅ Arnés completo (pkg={p.pkg}, llm={p.llm}, rigor={p.rigor}).")
        return 0
    return 1


# ------------------------------------------------------------------- verify-dep
def _pypi(nombre: str) -> dict[str, Any] | None:
    url = f"https://pypi.org/pypi/{nombre}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def cmd_verify_dep(args: argparse.Namespace) -> int:
    codigo = 0
    for nombre in args.paquetes:
        try:
            datos = _pypi(nombre)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"❓ {nombre}: no pude consultar PyPI ({exc}). Verifica a mano.")
            codigo = max(codigo, 2)
            continue
        if datos is None:
            print(f"❌ {nombre}: NO existe en PyPI. ¿Nombre alucinado? (slopsquatting)")
            codigo = 1
            continue
        info: dict[str, Any] = datos["info"]
        releases: dict[str, list[dict[str, Any]]] = datos.get("releases") or {}
        fechas = [
            archivo["upload_time_iso_8601"][:10]
            for archivos in releases.values()
            for archivo in archivos
            if archivo.get("upload_time_iso_8601")
        ]
        primera = min(fechas) if fechas else None
        edad = (dt.date.today() - dt.date.fromisoformat(primera)).days if primera else None
        urls = info.get("project_urls") or {}
        repo = next(
            (u for k, u in urls.items() if re.search(r"source|repo|code|github|home", k, re.I)),
            info.get("home_page") or "",
        )
        print(f"📦 {info['name']} {info['version']} — {info.get('summary') or ''}")
        print(
            f"   primera publicación: {primera or '?'} ({edad if edad is not None else '?'} días)"
        )
        print(f"   repositorio: {repo or '(no declarado)'}  ·  versiones: {len(releases)}")
        avisos = []
        if info["name"].lower().replace("_", "-") != nombre.lower().replace("_", "-"):
            avisos.append(f"el nombre canónico es {info['name']!r}")
        if edad is not None and edad < 90:
            avisos.append("tiene menos de 90 días: revisa con cuidado")
        if not repo:
            avisos.append("no declara repositorio")
        for a in avisos:
            print(f"   ⚠️  {a}")
        if avisos:
            codigo = max(codigo, 1)
    print("\nRecuerda: el humano aprueba toda dependencia nueva; justifícala en el PR.")
    return codigo


# ------------------------------------------------------------------- argparser
def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rumbo",
        description="RUMBO: Reconocer → Unificar → Modelar → Build con ANCLA → Operar.",
    )
    parser.add_argument("--version", action="version", version=f"rumbo {__version__}")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("init", help="monta el arnés RUMBO (Parte A)")
    p.add_argument("--dir", default=".", help="directorio del proyecto (por defecto: .)")
    p.add_argument("--nombre", help="nombre del proyecto")
    p.add_argument("--pkg", help="paquete Python en snake_case")
    llm = p.add_mutually_exclusive_group()
    llm.add_argument("--llm", dest="llm", action="store_true", default=None)
    llm.add_argument("--no-llm", dest="llm", action="store_false")
    p.add_argument("--proveedor", help="anthropic | openai | google | mistral | otro")
    p.add_argument("--sdk-paquete", help="paquete PyPI del SDK (proveedor 'otro')")
    p.add_argument("--sdk-modulo", help="módulo importable del SDK (proveedor 'otro')")
    p.add_argument("--rigor", choices=RIGORES)
    p.add_argument("--caso", help="caso concreto (texto libre)")
    p.add_argument("--yes", "-y", action="store_true", help="no preguntar; usar flags/defectos")
    p.add_argument("--no-install", action="store_true", help="no ejecutar uv ni pre-commit")
    p.add_argument("--force", action="store_true", help="sobrescribir archivos existentes")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new", help="crea un ADR, spec o postmortem desde su plantilla")
    p.add_argument("tipo", choices=sorted(_NUEVOS))
    p.add_argument("titulo")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("decision", help="anota una decisión humana en docs/decisiones.md")
    p.add_argument("titulo")
    p.add_argument("--porque", default="")
    p.add_argument("--quien", default="humano")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_decision)

    p = sub.add_parser("gate", help="evalúa un gate (G1..G5)")
    p.add_argument("codigo", type=str.upper, choices=sorted(GATES))
    p.add_argument("--dir", default=".")
    p.add_argument("--sin-ejecutar", action="store_true", help="G4: no ejecutar make check")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("doctor", help="verifica la estructura del arnés")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("check-tests", help="sensor anti-trampa", add_help=False)
    p.set_defaults(func=None)

    p = sub.add_parser("verify-dep", help="verifica paquetes en PyPI (anti-slopsquatting)")
    p.add_argument("paquetes", nargs="+")
    p.set_defaults(func=cmd_verify_dep)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check-tests":
        return check_tests_not_weakened.main(argv[1:])
    args = construir_parser().parse_args(argv)
    if args.comando == "init" and args.pkg and not pkg_valido(args.pkg):
        print(f"Error: paquete inválido {args.pkg!r}; usa snake_case.")
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
