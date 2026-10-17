"""Column dependency graph: ``{{...}}`` template parsing and validation.

Template syntax is ``{{Column Name}}`` (plan §4.3). ``{{Company}}`` and
``{{Website}}`` are RESERVED — they render the row's resolved identity,
never a column, so they never appear in ``depends_on``. Unknown references,
self-references and cycles are rejected at creation time; the graph is a
DAG because columns are append-only and forward references cannot exist.
"""

import re
from collections import deque
from collections.abc import Mapping, Sequence

RESERVED_REFS = frozenset({"Company", "Website"})

_REF_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


class TemplateError(ValueError):
    """An invalid prompt template.

    ``code`` is stable (``unknown_reference``, ``self_reference`` or
    ``cycle``); ``message`` names the offending reference.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_references(template: str) -> list[str]:
    """Every ``{{Name}}`` in template order, deduplicated.

    Reserved names are included — callers that want column dependencies
    use :func:`column_dependencies`.
    """
    names: list[str] = []
    for match in _REF_RE.finditer(template):
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


def column_dependencies(template: str) -> list[str]:
    """Non-reserved ``{{...}}`` references — the column's upstream columns."""
    return [name for name in parse_references(template) if name not in RESERVED_REFS]


class ColumnGraph:
    """Dependency graph over a sheet's columns, keyed by column name.

    Raising on construction is the validation: unknown references,
    self-references and cycles all surface as :class:`TemplateError`.
    """

    def __init__(self, templates: Mapping[str, str]) -> None:
        deps = {name: column_dependencies(template) for name, template in templates.items()}
        for name, upstream in deps.items():
            for dep in upstream:
                if dep == name:
                    raise TemplateError("self_reference", f"column {name!r} references itself")
                if dep not in templates:
                    raise TemplateError(
                        "unknown_reference",
                        f"column {name!r} references unknown column {dep!r}",
                    )
        self._deps = deps
        self._dependents: dict[str, list[str]] = {name: [] for name in deps}
        for name, upstream in deps.items():
            for dep in upstream:
                self._dependents[dep].append(name)
        self._order = self._topological_order()

    @classmethod
    def build(cls, columns: Sequence[tuple[str, str]]) -> "ColumnGraph":
        """Build from ``(name, prompt_template)`` pairs in position order."""
        return cls(dict(columns))

    def _topological_order(self) -> list[str]:
        """Kahn's algorithm over ``dep -> dependent`` edges, insertion order
        for ties so the result is deterministic."""
        remaining = {name: set(upstream) for name, upstream in self._deps.items()}
        available = deque(name for name in self._deps if not remaining[name])
        order: list[str] = []
        while available:
            name = available.popleft()
            order.append(name)
            for dependent in self._dependents[name]:
                remaining[dependent].discard(name)
                if not remaining[dependent] and dependent not in available:
                    available.append(dependent)
        if len(order) != len(self._deps):
            raise TemplateError("cycle", "column dependencies form a cycle")
        return order

    def dependencies(self, name: str) -> list[str]:
        """The column's direct upstream column names."""
        return list(self._deps[name])

    def dependents(self, name: str) -> list[str]:
        """Columns that directly reference ``name``."""
        return list(self._dependents[name])

    def transitive_dependents(self, name: str) -> list[str]:
        """Every column downstream of ``name``, in execution order."""
        downstream: set[str] = set()
        frontier = deque(self._dependents[name])
        while frontier:
            current = frontier.popleft()
            if current in downstream:
                continue
            downstream.add(current)
            frontier.extend(self._dependents[current])
        return [n for n in self._order if n in downstream]

    def execution_order(self) -> list[str]:
        """All column names, upstream before downstream (topological)."""
        return list(self._order)
