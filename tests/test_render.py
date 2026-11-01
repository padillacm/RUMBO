import pytest

from rumbo.render import TemplateError, render


def test_variables() -> None:
    assert render("hola {{nombre}}", {"nombre": "mundo"}) == "hola mundo"


def test_secciones_y_secciones_invertidas() -> None:
    plantilla = "a{{#x}}B{{/x}}{{^x}}C{{/x}}"
    assert render(plantilla, {"x": True}) == "aB"
    assert render(plantilla, {"x": False}) == "aC"


def test_etiquetas_solas_no_dejan_lineas_vacias() -> None:
    plantilla = "uno\n{{#x}}\ndos\n{{/x}}\ntres\n"
    assert render(plantilla, {"x": True}) == "uno\ndos\ntres\n"
    assert render(plantilla, {"x": False}) == "uno\ntres\n"


def test_secciones_anidadas_de_distinta_clave() -> None:
    assert render("{{#a}}{{#b}}ab{{/b}}{{/a}}", {"a": True, "b": True}) == "ab"
    assert render("{{#a}}{{#b}}ab{{/b}}{{/a}}", {"a": True, "b": False}) == ""


def test_no_toca_expresiones_de_github_actions() -> None:
    texto = "${{ secrets.{{clave}} }}"
    assert render(texto, {"clave": "API_KEY"}) == "${{ secrets.API_KEY }}"


def test_variable_desconocida_falla() -> None:
    with pytest.raises(TemplateError):
        render("{{nope}}", {})
    with pytest.raises(TemplateError):
        render("{{#nope}}x{{/nope}}", {})
