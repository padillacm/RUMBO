"""Tests del sensor anti-trampa (scripts/check_tests_not_weakened.py)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_tests_not_weakened", RAIZ / "scripts" / "check_tests_not_weakened.py"
)
assert _spec and _spec.loader
sensor = importlib.util.module_from_spec(_spec)
sys.modules["check_tests_not_weakened"] = sensor
_spec.loader.exec_module(sensor)

BASE = {
    "tests/test_a.py": "def test_uno():\n    assert 1\n    assert 2\n\nclass TestX:\n"
    "    def test_dos(self):\n        assert True\n",
    "tests/datos.json": None,
}


def test_sin_cambios_no_hay_problemas() -> None:
    inv = sensor.analizar(BASE)
    assert sensor.comparar(inv, sensor.analizar(dict(BASE))) == []
    assert inv.asserts == 3
    assert "tests/test_a.py::TestX.test_dos" in inv.tests


def test_detecta_test_eliminado() -> None:
    actual = dict(
        BASE, **{"tests/test_a.py": "def test_uno():\n    assert 1\n    assert 2\n    assert 3\n"}
    )
    problemas = sensor.comparar(sensor.analizar(BASE), sensor.analizar(actual))
    assert any("TestX.test_dos" in p for p in problemas)


def test_detecta_menos_asserts() -> None:
    actual = dict(
        BASE, **{"tests/test_a.py": BASE["tests/test_a.py"].replace("    assert 2\n", "")}
    )
    problemas = sensor.comparar(sensor.analizar(BASE), sensor.analizar(actual))
    assert any("asserts" in p for p in problemas)


def test_detecta_archivo_eliminado() -> None:
    actual = {"tests/test_a.py": BASE["tests/test_a.py"]}
    problemas = sensor.comparar(sensor.analizar(BASE), sensor.analizar(actual))
    assert problemas == ["archivo de tests eliminado: tests/datos.json"]


def test_agregar_tests_no_es_problema() -> None:
    actual = dict(BASE, **{"tests/test_b.py": "def test_nuevo():\n    assert 1\n"})
    assert sensor.comparar(sensor.analizar(BASE), sensor.analizar(actual)) == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_extremo_a_extremo_con_git(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tests").mkdir()
    archivo = tmp_path / "tests" / "test_x.py"
    archivo.write_text("def test_x():\n    assert 1\n    assert 2\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")

    archivo.write_text("def test_x():\n    assert 1\n")
    _git(tmp_path, "commit", "-qam", "debilita")
    assert sensor.main(["--root", str(tmp_path), "--base", "main"]) == 1

    _git(tmp_path, "commit", "-q", "--amend", "-m", "debilita [tests-change-approved]")
    assert sensor.main(["--root", str(tmp_path), "--base", "main"]) == 0


def test_sin_base_estricto_falla(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    assert sensor.main(["--root", str(tmp_path), "--strict", "--base", "no-existe"]) == 2
