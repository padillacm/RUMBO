from pathlib import Path

import pytest

from rumbo.cli import main
from rumbo.gates import evaluar
from rumbo.proyecto import MARCA_PLANTILLA, Proyecto, es_plantilla, seccion, slug


def test_init_no_interactivo(tmp_path: Path) -> None:
    rc = main(
        [
            "init",
            "--dir",
            str(tmp_path),
            "--yes",
            "--no-install",
            "--nombre",
            "Mi Proyecto",
            "--llm",
            "--proveedor",
            "anthropic",
            "--rigor",
            "produccion",
        ]
    )
    assert rc == 0
    proyecto = Proyecto.cargar(tmp_path)
    assert (proyecto.pkg, proyecto.llm, proyecto.rigor) == ("mi_proyecto", True, "produccion")
    assert "proveedor anthropic" in (tmp_path / "docs" / "decisiones.md").read_text()


def test_init_rechaza_paquete_invalido(tmp_path: Path) -> None:
    assert main(["init", "--dir", str(tmp_path), "--yes", "--pkg", "Mal-Nombre"]) == 2


def test_init_llm_sin_proveedor_falla(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["init", "--dir", str(tmp_path), "--yes", "--no-install", "--llm"])


def test_doctor(proyecto_llm: Path) -> None:
    assert main(["doctor", "--dir", str(proyecto_llm)]) == 0
    (proyecto_llm / "evals" / "run_evals.py").unlink()
    assert main(["doctor", "--dir", str(proyecto_llm)]) == 1


def test_new_numera_y_titula(proyecto_simple: Path) -> None:
    d = str(proyecto_simple)
    assert main(["new", "adr", "Usar DuckDB", "--dir", d]) == 0
    assert main(["new", "adr", "Workflow vs agente", "--dir", d]) == 0
    segundo = proyecto_simple / "docs" / "adr" / "0002-workflow-vs-agente.md"
    texto = segundo.read_text()
    assert texto.splitlines()[1] == "# ADR-0002: Workflow vs agente"
    assert MARCA_PLANTILLA in texto
    assert main(["new", "spec", "Consulta de precio", "--dir", d]) == 0
    assert (proyecto_simple / "specs" / "0001-consulta-de-precio.md").exists()


def test_decision(proyecto_simple: Path) -> None:
    assert (
        main(["decision", "Usar DuckDB", "--porque", "series", "--dir", str(proyecto_simple)]) == 0
    )
    texto = (proyecto_simple / "docs" / "decisiones.md").read_text()
    assert "Usar DuckDB" in texto
    assert "Por qué: series" in texto


def test_gate_g1_falla_con_plantilla_y_pasa_completo(proyecto_simple: Path) -> None:
    p = Proyecto.cargar(proyecto_simple)
    _, res = evaluar("G1", p)
    assert not all(r.ok for r in res)
    brief = proyecto_simple / "docs" / "product" / "brief.md"
    brief.write_text("\n".join(brief.read_text().splitlines()[1:]))
    _, res = evaluar("G1", p)
    assert all(r.ok for r in res)


def test_gate_g1_detecta_spikes_importados(proyecto_simple: Path) -> None:
    (proyecto_simple / "src" / "demo" / "malo.py").write_text("from spikes import prototipo\n")
    _, res = evaluar("G1", Proyecto.cargar(proyecto_simple))
    assert any(not r.ok and "spikes" in r.descripcion for r in res)


def test_gate_g2_exige_specs_ears(proyecto_llm: Path) -> None:
    p = Proyecto.cargar(proyecto_llm)
    _, res = evaluar("G2", p)
    assert any(not r.ok and "spec" in r.descripcion for r in res)
    assert any(not r.ok and "casos reales" in r.descripcion for r in res)


def test_gate_g3_ejecuta_sin_errores(proyecto_llm: Path) -> None:
    _, res = evaluar("G3", Proyecto.cargar(proyecto_llm))
    descripciones = {r.descripcion for r in res if r.ok}
    assert "contratos import-linter" in descripciones
    assert "C4 con diagramas Mermaid" in descripciones


def test_gate_cli_codigo_salida(proyecto_simple: Path) -> None:
    assert main(["gate", "g5", "--dir", str(proyecto_simple)]) == 1


def test_utilidades() -> None:
    assert slug("¿Precio de Bolsa?") == "precio-de-bolsa"
    assert es_plantilla(None)
    texto = "## Fitness function\n<!-- comentario -->\n\n## Otra\nx"
    assert seccion(texto, "Fitness function") == ""
    assert seccion("## Fitness function\ncontrato X\n", "Fitness function") == "contrato X"
