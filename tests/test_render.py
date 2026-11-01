import pytest

from rumbo.render import TemplateError, render


def test_variables() -> None:
    assert render("hello {{name}}", {"name": "world"}) == "hello world"


def test_sections_and_inverted_sections() -> None:
    template = "a{{#x}}B{{/x}}{{^x}}C{{/x}}"
    assert render(template, {"x": True}) == "aB"
    assert render(template, {"x": False}) == "aC"


def test_standalone_tags_leave_no_blank_lines() -> None:
    template = "one\n{{#x}}\ntwo\n{{/x}}\nthree\n"
    assert render(template, {"x": True}) == "one\ntwo\nthree\n"
    assert render(template, {"x": False}) == "one\nthree\n"


def test_nested_sections_with_different_keys() -> None:
    assert render("{{#a}}{{#b}}ab{{/b}}{{/a}}", {"a": True, "b": True}) == "ab"
    assert render("{{#a}}{{#b}}ab{{/b}}{{/a}}", {"a": True, "b": False}) == ""


def test_github_actions_expressions_are_untouched() -> None:
    text = "${{ secrets.{{key}} }}"
    assert render(text, {"key": "API_KEY"}) == "${{ secrets.API_KEY }}"


def test_prefix_variable_names_do_not_collide() -> None:
    assert render("{{pkg}}-{{pkg_x}}", {"pkg": "a", "pkg_x": "b"}) == "a-b"


def test_substituted_values_are_not_expanded_again() -> None:
    ctx = {"a": True, "name": "Uses {{mustache}} templates", "b": "{{c}}", "c": "X"}
    assert render("{{#a}}{{name}}{{/a}}", ctx) == "Uses {{mustache}} templates"
    assert render("{{^a}}x{{/a}}{{b}}", ctx) == "{{c}}"
    assert render("{{#a}}{{b}}{{/a}}", ctx) == "{{c}}"


def test_dropped_sections_are_not_validated() -> None:
    # A falsy section may mention variables the context does not have.
    assert render("{{#a}}{{nope}}{{/a}}ok", {"a": False}) == "ok"


@pytest.mark.parametrize(
    "template",
    [
        "{{nope}}",
        "{{#nope}}x{{/nope}}",
        "{{#a}}x",
        "{{#a}}x{{/b}}",
        "{{#a}}X{{#a}}Y{{/a}}Z{{/a}}",
        "x{{/a}}",
        "{{#b}}{{#a}}Y{{/b}}{{/a}}",
    ],
)
def test_bad_templates_raise(template: str) -> None:
    with pytest.raises(TemplateError):
        render(template, {"a": True})
