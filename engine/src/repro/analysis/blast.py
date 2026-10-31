"""Blast radius: the changed symbols and the modules that import them (ARCHITECTURE §9).

A change's modules are the modules of its symbols, before and after (a moved symbol leaves one
and enters another). Their importers are affected at distance 1, the importers of those at 2,
and so on, over the import edges of both sides together: an importer that a change removed
was still affected by it. Static imports are module-level, so an affected module appears as
its ``<module>`` symbol; symbol-level dependents come from the dynamic map (Stage E).

The cockpit's graph is both sides too: every module, every import, and a node per changed
function, method or class. Once the tests are selected, a node per selected test and a
``covers`` edge from it to each changed symbol's node that selected it (``selection_graph``):
the points the stage's bunches of strands fire from (B14).
"""

import re
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from repro.analysis.changes import SymbolChange
from repro.analysis.imports import ImportGraph
from repro.analysis.symbols import MODULE_QUALNAME


def module_of(symbol_id: str) -> str:
    return symbol_id.partition(":")[0]


def module_symbol(module: str) -> str:
    return f"{module}:{MODULE_QUALNAME}#0"


@dataclass(frozen=True, slots=True)
class BlastRadius:
    changed: tuple[str, ...]
    affected: tuple[tuple[str, int], ...]  # (symbol_id, distance), nearest first
    module_level_changes: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        """The ``blast.computed`` payload."""
        return {
            "changed": list(self.changed),
            "affected": [{"symbol_id": s, "distance": d} for s, d in self.affected],
            "module_level_changes": list(self.module_level_changes),
        }


def downstream(
    sources: Iterable[str], base: ImportGraph, head: ImportGraph, *, packages: bool = False
) -> dict[str, int]:
    """``sources`` at distance 0, their importers at 1, and so on, over both sides' imports.

    With ``packages`` (Python's selection), a module also imports the package it is in, as
    Python's import system does before it runs the module: ``from a.b import c`` runs
    ``a/__init__.py`` and ``a/b/__init__.py`` first, though the graphs hold only the edge to
    ``a.b.c``. The blast radius shown keeps the static imports alone."""
    importers: dict[str, set[str]] = {}
    for importer, imported in base.imports | head.imports:
        importers.setdefault(imported, set()).add(importer)
    if packages:
        known = base.modules.keys() | head.modules.keys()
        for module in known:
            parent = module.rpartition(".")[0]
            while parent and parent not in known:
                parent = parent.rpartition(".")[0]
            if parent:
                importers.setdefault(parent, set()).add(module)
    start = sorted(set(sources))
    distance = dict.fromkeys(start, 0)
    queue = deque(start)
    while queue:
        module = queue.popleft()
        for importer in sorted(importers.get(module, ())):
            if importer not in distance:
                distance[importer] = distance[module] + 1
                queue.append(importer)
    return distance


def blast_radius(
    changes: Sequence[SymbolChange], base: ImportGraph, head: ImportGraph
) -> BlastRadius:
    changed = tuple(dict.fromkeys(change.symbol_id for change in changes))
    ids = [i for c in changes for i in (c.symbol_id, c.previous_symbol_id) if i is not None]
    module_level = sorted({module_of(i) for i in ids if f":{MODULE_QUALNAME}#" in i})
    distance = downstream((module_of(i) for i in ids), base, head)
    excluded = set(changed)
    affected = sorted(
        (
            (module_symbol(module), d)
            for module, d in distance.items()
            if d > 0 and module_symbol(module) not in excluded
        ),
        key=lambda item: (item[1], item[0]),
    )
    return BlastRadius(changed, tuple(affected), tuple(module_level))


def graph_payloads(
    base: ImportGraph, head: ImportGraph, changes: Sequence[SymbolChange]
) -> list[tuple[str, dict[str, Any]]]:
    """``graph.node`` and ``graph.edge`` payloads for both sides: modules, then changed
    functions, methods and classes, then imports (head's file wins for a module on both)."""
    modules = {**base.modules, **head.modules}
    events: list[tuple[str, dict[str, Any]]] = [
        ("graph.node", {"node": _node(name, "module", name, file, None)})
        for name, file in sorted(modules.items())
    ]
    for change in changes:
        if change.kind == "module":
            continue  # the module's own node stands for it
        lines = change.head_lines or change.base_lines
        qualname = change.symbol_id.partition(":")[2].rpartition("#")[0]
        line = lines[0] if lines else None
        node = _node(change.symbol_id, change.kind, qualname, change.file, line)
        events.append(("graph.node", {"node": node}))
    events.extend(
        ("graph.edge", {"source": importer, "target": imported, "type": "imports"})
        for importer, imported in sorted(base.imports | head.imports)
    )
    return events


#: Most test nodes one run adds to the graph: the stress fixture's 600, the count the cockpit's
#: performance budget is measured with (docs/DESIGN.md). A larger selection keeps the tests
#: selected for the most changed symbols; the rest are still selected and run, only not drawn.
MAX_TEST_NODES: Final = 600
#: The schema's bounds on a node: its ID's length, and its file's ``RelPath`` pattern.
_MAX_ID: Final = 1024
_REL_PATH: Final = re.compile(r"^(?![A-Za-z]:)(?!/)(?!.*\\)(?!(.*/)?\.\.(/|$)).+$")


@dataclass(frozen=True, slots=True)
class SelectionGraph:
    """A selection's test nodes and ``covers`` edges, and how many tests have no node."""

    events: tuple[tuple[str, dict[str, Any]], ...]
    left_out: int


def selection_graph(
    selected: Iterable[tuple[str, Iterable[str]]],
    changes: Sequence[SymbolChange],
    limit: int = MAX_TEST_NODES,
) -> SelectionGraph:
    """A ``graph.node`` per selected test, then a ``covers`` edge from it to each changed
    symbol's node behind its selection, from ``(test ID, symbols)`` pairs (``test.selected``'s
    target and ``symbols``).

    A test node's ID is its test ID, as events name it in ``target.test``; its file is the part
    before the first ``::``, its name the rest. A symbol links only if ``graph_payloads`` drew a
    node for it: a changed function, method or class, named on either side (a moved symbol's
    base ID is its node's too). Module-level symbols have no node of their own. Each test comes
    once, however often it is given. Past ``limit``, the tests selected for the most linked
    symbols are kept (then by ID); a test ID the schema cannot hold as a node is left out."""
    node_of: dict[str, str] = {}
    for change in changes:
        if change.kind == "module":
            continue
        node_of[change.symbol_id] = change.symbol_id
        if change.previous_symbol_id is not None:
            node_of.setdefault(change.previous_symbol_id, change.symbol_id)
    found: dict[str, tuple[str, ...]] = {}
    for test, symbols in selected:
        linked = (node_of[s] for s in symbols if s in node_of)
        found[test] = tuple(dict.fromkeys((*found.get(test, ()), *linked)))
    places = {test: place for test in found if (place := _test_place(test)) is not None}
    kept = sorted(places, key=lambda test: (-len(found[test]), test))[: max(0, limit)]
    tests = sorted(kept)
    events: list[tuple[str, dict[str, Any]]] = []
    for test in tests:
        file, name = places[test]
        events.append(("graph.node", {"node": _node(test, "test", name, file, None)}))
    events.extend(
        ("graph.edge", {"source": test, "target": symbol, "type": "covers"})
        for test in tests
        for symbol in sorted(found[test])
    )
    return SelectionGraph(tuple(events), len(found) - len(tests))


def _test_place(test: str) -> tuple[str, str] | None:
    """A test ID's file and name (``t/test_x.py::TestX::test_y``: ``t/test_x.py`` and
    ``TestX.test_y``), or None if the schema cannot hold it as a node."""
    file, sep, rest = test.partition("::")
    name = rest.replace("::", ".") if sep else test
    if len(test) > _MAX_ID or not name or not _REL_PATH.match(file):
        return None
    return file, name


def _node(
    node_id: str, kind: str, name: str, file: str, line: int | None
) -> dict[str, str | int | None]:
    return {"id": node_id, "type": kind, "name": name, "file": file, "line": line}
