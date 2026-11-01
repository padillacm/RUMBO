import ast
import itertools
import tomllib
from pathlib import Path

import pytest

from rumbo.scaffold import (
    PROVEEDORES,
    Config,
    fusionar_lineas,
    fusionar_pyproject,
    pkg_valido,
    plan_archivos,
    ruta_destino,
    scaffold,
)

COMBINACIONES = [
    Config(nombre="P", pkg="paquete", llm=llm, proveedor=prov, rigor=rigor)
    for (llm, prov), rigor in itertools.product(
        [(False, ""), *((True, p) for p in PROVEEDORES), (True, "otro")],
        ["prototipo", "mvp", "produccion"],
    )
]


@pytest.mark.parametrize(
    "cfg", COMBINACIONES, ids=lambda c: f"{c.proveedor or 'sin-llm'}-{c.rigor}"
)
def test_todas_las_combinaciones_renderizan(cfg: Config) -> None:
    plan = plan_archivos(cfg)
    for ruta, contenido in plan.items():
        texto = contenido if isinstance(contenido, str) else Path(contenido).read_text()
        if isinstance(contenido, str):
            assert "{{" not in texto.replace("${{", ""), ruta
        assert "__pkg__" not in ruta
        if ruta.endswith(".py"):
            ast.parse(texto, filename=ruta)
        if ruta.endswith(".toml"):
            tomllib.loads(texto)
    datos = tomllib.loads(str(plan["pyproject.toml"]))
    assert datos["tool"]["rumbo"] == {
        "version": datos["tool"]["rumbo"]["version"],
        "pkg": "paquete",
        "llm": cfg.llm,
        "proveedor": cfg.proveedor or "ninguno",
        "rigor": cfg.rigor,
    }


def test_archivos_condicionados_a_llm() -> None:
    con = plan_archivos(Config(nombre="x", pkg="x", llm=True, proveedor="anthropic"))
    sin = plan_archivos(Config(nombre="x", pkg="x", llm=False))
    llm_only = {
        "evals/run_evals.py",
        "evals/datasets/smoke.jsonl",
        "src/x/prompts/__init__.py",
        "src/x/app/entrypoint.py",
        "docs/evals/plan.md",
    }
    assert llm_only <= con.keys()
    assert not llm_only & sin.keys()


def test_nightly_y_dependabot_segun_rigor() -> None:
    proto = plan_archivos(Config(nombre="x", pkg="x", llm=False, rigor="prototipo"))
    mvp = plan_archivos(Config(nombre="x", pkg="x", llm=False, rigor="mvp"))
    assert ".github/workflows/nightly.yml" not in proto
    assert ".github/dependabot.yml" not in proto
    assert ".github/workflows/nightly.yml" in mvp


def test_check_segun_rigor_y_llm() -> None:
    def linea_check(cfg: Config) -> str:
        makefile = str(plan_archivos(cfg)["Makefile"])
        return next(ln for ln in makefile.splitlines() if ln.startswith("check:"))

    assert linea_check(Config(nombre="x", pkg="x", llm=False, rigor="prototipo")) == (
        "check: lint types test arch"
    )
    assert linea_check(
        Config(nombre="x", pkg="x", llm=True, proveedor="openai", rigor="produccion")
    ) == ("check: lint types test arch security evals")


def test_sdk_del_proveedor_prohibido_en_dominio() -> None:
    cfg = Config(nombre="x", pkg="x", llm=True, proveedor="google")
    datos = tomllib.loads(str(plan_archivos(cfg)["pyproject.toml"]))
    contratos = datos["tool"]["importlinter"]["contracts"]
    puro = next(c for c in contratos if c["type"] == "forbidden")
    assert "google" in puro["forbidden_modules"]
    assert cfg.sdk_paquete == "google-genai"


def test_ruta_destino() -> None:
    cfg = Config(nombre="x", pkg="mi_pkg", llm=False)
    assert ruta_destino("dot_github/workflows/ci.yml.tmpl", cfg) == ".github/workflows/ci.yml"
    assert ruta_destino("src/__pkg__/domain/__init__.py", cfg) == "src/mi_pkg/domain/__init__.py"


@pytest.mark.parametrize("pkg", ["MiPkg", "1pkg", "con-guion", "class", "tests", ""])
def test_paquetes_invalidos(pkg: str) -> None:
    assert not pkg_valido(pkg)
    with pytest.raises(ValueError):
        Config(nombre="x", pkg=pkg, llm=False)


def test_rigor_invalido() -> None:
    with pytest.raises(ValueError):
        Config(nombre="x", pkg="x", llm=False, rigor="extremo")


def test_scaffold_no_sobrescribe_y_reporta(proyecto_simple: Path) -> None:
    agents = proyecto_simple / "AGENTS.md"
    agents.write_text("mío")
    reporte = scaffold(
        Config(nombre="Demo", pkg="demo", llm=False, rigor="prototipo"), proyecto_simple
    )
    assert agents.read_text() == "mío"
    assert "AGENTS.md" in reporte.omitidos
    assert "pyproject.toml" in reporte.omitidos
    assert not reporte.creados
    assert not reporte.fusionados


def test_sensor_copiado_y_ejecutable(proyecto_simple: Path) -> None:
    sensor = proyecto_simple / "scripts" / "check_tests_not_weakened.py"
    assert sensor.exists()
    assert sensor.stat().st_mode & 0o111


def test_fusionar_pyproject_respeta_secciones_existentes() -> None:
    existente = '[project]\nname = "x"\n\n[tool.ruff]\nline-length = 120\n'
    plantilla = (
        '[tool.ruff]\nline-length = 100\n\n[tool.ruff.lint]\nselect = ["E"]\n\n'
        '[tool.rumbo]\npkg = "x"\n'
    )
    salida, notas = fusionar_pyproject(existente, plantilla)
    datos = tomllib.loads(salida)
    assert datos["tool"]["ruff"] == {"line-length": 120}
    assert datos["tool"]["rumbo"] == {"pkg": "x"}
    assert any("conservado" in n for n in notas)


def test_fusionar_lineas_sin_duplicar() -> None:
    salida, nuevas = fusionar_lineas(".venv/\n", ".venv/\n.env\n")
    assert nuevas == [".env"]
    assert salida.count(".venv/") == 1
