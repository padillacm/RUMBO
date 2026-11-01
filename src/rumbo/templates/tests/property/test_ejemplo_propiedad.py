"""Ejemplo de test de propiedades con Hypothesis.

Usa Hypothesis para invariantes del dominio (p. ej., "la unión de bloques de fechas cubre
exactamente el rango, sin solapes"). Este ejemplo verifica una propiedad trivial; reemplázalo.
"""

from hypothesis import given
from hypothesis import strategies as st


@given(st.lists(st.integers()))
def test_ordenar_es_idempotente(xs: list[int]) -> None:
    una_vez = sorted(xs)
    assert sorted(una_vez) == una_vez
