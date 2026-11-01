"""Monta el arnés RUMBO (Parte A) en un directorio de proyecto."""

from __future__ import annotations

import datetime as dt
import keyword
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rumbo import __version__
from rumbo.render import render

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
SENSOR_FILE = PACKAGE_DIR / "sensors" / "check_tests_not_weakened.py"

RIGORES = ("prototipo", "mvp", "produccion")


@dataclass(frozen=True)
class Proveedor:
    paquete: str  # nombre en PyPI
    modulo: str  # módulo raíz que se importa
    env_key: str  # variable de entorno con la API key


PROVEEDORES: dict[str, Proveedor] = {
    "anthropic": Proveedor("anthropic", "anthropic", "ANTHROPIC_API_KEY"),
    "openai": Proveedor("openai", "openai", "OPENAI_API_KEY"),
    "google": Proveedor("google-genai", "google", "GEMINI_API_KEY"),
    "mistral": Proveedor("mistralai", "mistralai", "MISTRAL_API_KEY"),
}

DEV_DEPS = [
    "ruff",
    "pyright",
    "pytest",
    "pytest-cov",
    "hypothesis",
    "import-linter",
    "pre-commit",
    "pip-audit",
    "bandit",
    "mutmut",
]
LLM_DEPS = ["pydantic", "fastapi", "httpx"]

# Archivos que solo se generan si se cumple la condición (prefijo de ruta de plantilla).
CONDICIONES: dict[str, Callable[[Config], bool]] = {
    "evals/": lambda c: c.llm,
    "docs/evals/": lambda c: c.llm,
    "src/__pkg__/prompts/": lambda c: c.llm,
    "src/__pkg__/app/entrypoint.py": lambda c: c.llm,
    "src/__pkg__/api/http.py": lambda c: c.llm,
    "tests/unit/test_graders.py": lambda c: c.llm,
    "tests/unit/test_entrypoint.py": lambda c: c.llm,
    "tests/unit/test_prompts.py": lambda c: c.llm,
    "dot_github/workflows/nightly.yml": lambda c: c.rigor != "prototipo",
    "dot_github/dependabot.yml": lambda c: c.rigor != "prototipo",
}

_PKG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVADOS = {"test", "tests", "src", "evals", "scripts", "spikes", "domain", "app", "api"}


def pkg_valido(pkg: str) -> bool:
    return bool(_PKG_RE.match(pkg)) and not keyword.iskeyword(pkg) and pkg not in _RESERVADOS


@dataclass
class Config:
    nombre: str
    pkg: str
    llm: bool
    proveedor: str = ""
    rigor: str = "mvp"
    caso: str = ""
    sdk_paquete: str = ""
    sdk_modulo: str = ""
    sdk_env_key: str = ""

    def __post_init__(self) -> None:
        if not pkg_valido(self.pkg):
            raise ValueError(f"nombre de paquete inválido (usa snake_case): {self.pkg!r}")
        if self.rigor not in RIGORES:
            raise ValueError(f"rigor inválido: {self.rigor!r}; usa uno de {RIGORES}")
        if not self.llm:
            self.proveedor = ""
            self.sdk_paquete = self.sdk_modulo = self.sdk_env_key = ""
            return
        prov = PROVEEDORES.get(self.proveedor)
        if prov:
            self.sdk_paquete = self.sdk_paquete or prov.paquete
            self.sdk_modulo = self.sdk_modulo or prov.modulo
            self.sdk_env_key = self.sdk_env_key or prov.env_key
        self.sdk_modulo = self.sdk_modulo.split(".")[0]

    def forbidden_dominio(self) -> list[str]:
        mods = ["httpx", "requests", "fastapi"]
        if self.sdk_modulo and self.sdk_modulo not in mods:
            mods.append(self.sdk_modulo)
        return mods

    def contexto(self) -> dict[str, object]:
        forb = self.forbidden_dominio()
        return {
            "nombre": self.nombre,
            "pkg": self.pkg,
            "llm": self.llm,
            "proveedor": self.proveedor or "ninguno",
            "sdk_paquete": self.sdk_paquete,
            "sdk_modulo": self.sdk_modulo,
            "sdk_env_key": self.sdk_env_key,
            "rigor": self.rigor,
            "prototipo": self.rigor == "prototipo",
            "mvp": self.rigor == "mvp",
            "produccion": self.rigor == "produccion",
            "mvp_o_mas": self.rigor != "prototipo",
            "caso": self.caso or "(sin caso concreto todavía)",
            "fecha": dt.date.today().isoformat(),
            "rumbo_version": __version__,
            "forbidden_toml": ", ".join(f'"{m}"' for m in forb),
            "forbidden_py": ", ".join(f'"{m}"' for m in forb),
        }


@dataclass
class Reporte:
    creados: list[str] = field(default_factory=list)
    omitidos: list[str] = field(default_factory=list)
    fusionados: list[str] = field(default_factory=list)
    comandos: list[tuple[str, int]] = field(default_factory=list)

    def resumen(self) -> str:
        lineas = [f"Creados: {len(self.creados)}"]
        if self.fusionados:
            lineas.append("Fusionados (revísalos):")
            lineas += [f"  - {p}" for p in self.fusionados]
        if self.omitidos:
            lineas.append("Ya existían, no se tocaron (revisa a mano):")
            lineas += [f"  - {p}" for p in self.omitidos]
        fallidos = [c for c, rc in self.comandos if rc != 0]
        if fallidos:
            lineas.append("Comandos que fallaron (ver salida arriba):")
            lineas += [f"  - {c}" for c in fallidos]
        return "\n".join(lineas)


def ruta_destino(rel: str, cfg: Config) -> str:
    partes = []
    for parte in rel.split("/"):
        parte = parte.replace("__pkg__", cfg.pkg)
        if parte.startswith("dot_"):
            parte = "." + parte[4:]
        partes.append(parte)
    destino = "/".join(partes)
    return destino.removesuffix(".tmpl")


def _incluir(rel: str, cfg: Config) -> bool:
    rel = rel.removesuffix(".tmpl")
    return all(cond(cfg) for pref, cond in CONDICIONES.items() if rel.startswith(pref))


def plan_archivos(cfg: Config) -> dict[str, str | Path]:
    """Devuelve {ruta destino: contenido renderizado o ruta a copiar tal cual}."""
    ctx = cfg.contexto()
    plan: dict[str, str | Path] = {}
    for src in sorted(TEMPLATES_DIR.rglob("*")):
        if src.is_dir() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(TEMPLATES_DIR).as_posix()
        if not _incluir(rel, cfg):
            continue
        destino = ruta_destino(rel, cfg)
        if rel.endswith(".tmpl"):
            plan[destino] = render(src.read_text(encoding="utf-8"), ctx)
        else:
            plan[destino] = src
    plan["scripts/check_tests_not_weakened.py"] = SENSOR_FILE
    plan["RUMBO.md"] = rumbo_md()
    return plan


def rumbo_md() -> Path:
    for cand in (TEMPLATES_DIR / "RUMBO.md", PACKAGE_DIR.parents[1] / "RUMBO.md"):
        if cand.exists():
            return cand
    raise FileNotFoundError("No encuentro RUMBO.md en el paquete")


def _bloques_tool(texto: str) -> list[tuple[str, str]]:
    """Separa un pyproject en bloques [tool.X...] → (raíz 'tool.X', texto)."""
    bloques: list[tuple[str, str]] = []
    actual: list[str] = []
    raiz = ""
    for linea in texto.splitlines(keepends=True):
        m = re.match(r"^\[\[?\s*([\w.\-\"]+)\s*\]\]?", linea)
        if m:
            if raiz.startswith("tool.") and actual:
                bloques.append((raiz, "".join(actual)))
            nombre = m.group(1)
            raiz = ".".join(nombre.split(".")[:2])
            actual = [linea]
        else:
            actual.append(linea)
    if raiz.startswith("tool.") and actual:
        bloques.append((raiz, "".join(actual)))
    return bloques


def fusionar_pyproject(existente: str, plantilla: str) -> tuple[str, list[str]]:
    """Añade al pyproject existente las secciones [tool.*] de RUMBO que no tenga.

    Devuelve el texto fusionado y la lista de notas (secciones añadidas y conservadas).
    """
    datos = tomllib.loads(existente)
    presentes = {f"tool.{k}" for k in datos.get("tool", {})}
    backend = str(datos.get("build-system", {}).get("build-backend", ""))
    agregados: dict[str, list[str]] = {}
    conservados: set[str] = set()
    for raiz, bloque in _bloques_tool(plantilla):
        if raiz == "tool.hatch" and backend and "hatchling" not in backend:
            continue
        if raiz in presentes:
            conservados.add(raiz)
        else:
            agregados.setdefault(raiz, []).append(bloque)
    notas = [f"+[{r}]" for r in agregados] + [
        f"conservado tu [{r}] (compáralo con RUMBO.md A2)" for r in sorted(conservados)
    ]
    if not agregados:
        return existente, notas
    extra = "".join(b for bloques in agregados.values() for b in bloques)
    salida = existente.rstrip("\n") + "\n\n# --- Añadido por rumbo init ---\n" + extra
    tomllib.loads(salida)  # falla si el resultado no es TOML válido
    return salida, notas


def fusionar_lineas(existente: str, plantilla: str) -> tuple[str, list[str]]:
    actuales = {ln.strip() for ln in existente.splitlines()}
    nuevas = [ln for ln in plantilla.splitlines() if ln.strip() and ln.strip() not in actuales]
    if not nuevas:
        return existente, []
    salida = existente.rstrip("\n") + "\n\n# --- Añadido por rumbo init ---\n"
    return salida + "\n".join(nuevas) + "\n", nuevas


def scaffold(cfg: Config, destino: Path, forzar: bool = False) -> Reporte:
    reporte = Reporte()
    destino.mkdir(parents=True, exist_ok=True)
    for rel, contenido in plan_archivos(cfg).items():
        objetivo = destino / rel
        texto = contenido if isinstance(contenido, str) else None
        if objetivo.exists() and not forzar:
            if rel in ("pyproject.toml", ".gitignore"):
                nuevo = texto if texto is not None else Path(contenido).read_text()
                actual = objetivo.read_text(encoding="utf-8")
                fusion = fusionar_pyproject if rel == "pyproject.toml" else fusionar_lineas
                salida, notas = fusion(actual, nuevo)
                if salida != actual:
                    objetivo.write_text(salida, encoding="utf-8")
                    detalle = (
                        "; ".join(notas) if rel == "pyproject.toml" else f"+{len(notas)} líneas"
                    )
                    reporte.fusionados.append(f"{rel} ({detalle})")
                    continue
            reporte.omitidos.append(rel)
            continue
        objetivo.parent.mkdir(parents=True, exist_ok=True)
        if texto is not None:
            objetivo.write_text(texto, encoding="utf-8")
        else:
            shutil.copyfile(contenido, objetivo)
        if rel.startswith("scripts/"):
            objetivo.chmod(0o755)
        reporte.creados.append(rel)
    return reporte


def _correr(cmd: list[str], cwd: Path, reporte: Reporte) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    try:
        rc = subprocess.run(cmd, cwd=cwd, check=False).returncode
    except FileNotFoundError:
        rc = 127
        print(f"  (no se encontró {cmd[0]!r})")
    reporte.comandos.append((" ".join(cmd), rc))
    return rc


def instalar(cfg: Config, destino: Path, reporte: Reporte) -> None:
    """Instala dependencias con uv (últimas versiones estables) y los hooks de pre-commit."""
    if shutil.which("uv") is None:
        print("AVISO: 'uv' no está instalado; instala uv y ejecuta los comandos de README.md.")
        reporte.comandos.append(("uv (no instalado)", 127))
        return
    _correr(["uv", "add", "--dev", *DEV_DEPS], destino, reporte)
    if cfg.llm:
        runtime = list(LLM_DEPS)
        if cfg.sdk_paquete:
            runtime.append(cfg.sdk_paquete)
        _correr(["uv", "add", *runtime], destino, reporte)
    if (destino / ".git").exists():
        _correr(["uv", "run", "pre-commit", "autoupdate"], destino, reporte)
        _correr(["uv", "run", "pre-commit", "install"], destino, reporte)
    else:
        print("AVISO: no es un repositorio git; ejecuta 'git init' y luego 'make hooks'.")
