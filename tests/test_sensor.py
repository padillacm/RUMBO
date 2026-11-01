"""El sensor anti-trampa se prueba con los mismos tests que se copian a cada proyecto."""

import subprocess
import sys
from pathlib import Path


def test_tests_del_sensor_pasan_en_proyecto_generado(proyecto_simple: Path) -> None:
    resultado = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
            "tests/unit/test_check_tests_not_weakened.py",
        ],
        cwd=proyecto_simple,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "7 passed" in resultado.stdout
