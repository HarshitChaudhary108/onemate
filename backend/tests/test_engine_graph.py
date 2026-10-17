"""Template parsing and dependency graph tests (plan §4.3)."""

import pytest

from app.engine.graph import (
    RESERVED_REFS,
    ColumnGraph,
    TemplateError,
    column_dependencies,
    parse_references,
)


def test_parse_references_dedupes_in_order() -> None:
    template = "Sum {{Revenue}} then {{Revenue}} vs {{Company}} and {{ Employees }}"
    assert parse_references(template) == ["Revenue", "Company", "Employees"]


def test_column_dependencies_excludes_reserved() -> None:
    template = "About {{Company}} ({{Website}}): {{Revenue}}"
    assert column_dependencies(template) == ["Revenue"]
    assert RESERVED_REFS == frozenset({"Company", "Website"})


def test_unknown_reference_rejected() -> None:
    with pytest.raises(TemplateError) as exc:
        ColumnGraph({"A": "uses {{Ghost}}", "B": "plain"})
    assert exc.value.code == "unknown_reference"
    assert "Ghost" in exc.value.message


def test_self_reference_rejected() -> None:
    with pytest.raises(TemplateError) as exc:
        ColumnGraph({"A": "uses {{A}}"})
    assert exc.value.code == "self_reference"


def test_cycle_rejected() -> None:
    with pytest.raises(TemplateError) as exc:
        ColumnGraph({"A": "uses {{B}}", "B": "uses {{A}}"})
    assert exc.value.code == "cycle"


def test_three_node_cycle_rejected() -> None:
    templates = {"A": "{{B}}", "B": "{{C}}", "C": "{{A}}"}
    with pytest.raises(TemplateError) as exc:
        ColumnGraph(templates)
    assert exc.value.code == "cycle"


def test_execution_order_upstream_first() -> None:
    graph = ColumnGraph({"C": "{{B}} + {{A}}", "A": "a", "B": "{{A}} b"})
    assert graph.execution_order() == ["A", "B", "C"]
    assert graph.dependencies("C") == ["B", "A"]
    assert graph.dependencies("A") == []


def test_dependents_and_transitive_dependents() -> None:
    graph = ColumnGraph({"A": "a", "B": "{{A}}", "C": "{{B}}", "D": "d"})
    assert graph.dependents("A") == ["B"]
    assert graph.transitive_dependents("A") == ["B", "C"]
    assert graph.transitive_dependents("D") == []


def test_reserved_refs_never_become_dependencies() -> None:
    graph = ColumnGraph({"A": "about {{Company}} on {{Website}}", "B": "{{A}}"})
    assert graph.dependencies("A") == []
    assert graph.execution_order() == ["A", "B"]
