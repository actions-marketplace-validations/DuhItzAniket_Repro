"""Test selection (D05, ARCHITECTURE §4.1): the tests a change can affect, and the changed code
no selected test runs.

- ``blast_radius``: tests that ran a changed function, method or class on base (the map). A
  class's own code (its body: attributes, bases, decorators) runs at import, so a change to it
  reaches the tests that ran one of its methods (H06), as coverage counts it below;
- ``module_import``: tests whose module imports, directly or not, a module whose own code
  changed (the import graphs of C07, where a module imports its package first, as Python does:
  ``from pkg import sub`` runs ``pkg/__init__.py``), or a class whose body is all its code; or
  tests that sit below a changed ``conftest.py``;
- ``new_test``, ``modified_test``: tests the change added, or whose function it changed.

The map is each test's *places* (``repro.runner.pytest``), turned into the symbols of C06 here:
a place's file gives the module and its symbol table, its qualified name the symbol, and its
first line the ordinal when a name repeats. Code with no symbol of its own (a lambda, a
comprehension, an annotation scope) belongs to the nearest enclosing symbol.

A changed symbol is **covered** when a selected test ran it, on base (the map) or on head (the
selected tests' own map there). The methods dataclasses, attrs and ``NamedTuple`` write for a
class are mapped as the class (the plugin), so constructing one runs it.

**Code that runs at import** cannot be in any test's map: a module is imported once per
process, mostly while pytest collects, before any test starts. So:
- a module's own code (a package's ``__init__.py`` too, a docstring alone included) is covered
  when a selected test's module imports it, directly or not, packages imported first;
- a class's own code (its body) runs at import too. A class that has code to run later (a
  method of its own, or one written for it) is covered when a selected test ran some of it. A
  class whose body is all its code (``symbols.body_only``: an enum's members, an exception
  class, a ``Protocol``; no method, decorator or ``NamedTuple`` base) has nothing a test can be
  seen to run, so it is treated as module-level code: selected and covered by imports.

What is not covered is a ``no_covering_test`` item: an explicit gap on the card, never a pass.
Test code itself (the modules tests are collected from, and ``conftest.py``) is not asked to be
covered.
"""

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

from repro.analysis.blast import downstream, module_of
from repro.analysis.changes import SymbolChange
from repro.analysis.imports import ImportGraph
from repro.analysis.modules import module_names
from repro.analysis.symbols import MODULE_QUALNAME, SymbolTable, build_table
from repro.gitops import Git, read_blobs, regular_files
from repro.runner.pytest import Place, PytestRun

SelectionReason = Literal["blast_radius", "module_import", "new_test", "modified_test"]
REASONS: Final[tuple[SelectionReason, ...]] = (
    "blast_radius",
    "module_import",
    "new_test",
    "modified_test",
)
#: Bumped whenever what a cached map holds, or how it is made, changes (3: methods written for
#: a class, mapped as the class).
MAP_FORMAT: Final = 3
CONFTEST: Final = "conftest.py"


def _parent(qualname: str) -> str:
    """The enclosing name: ``f.<locals>.g`` -> ``f``, ``C.m`` -> ``C``, ``f`` -> ``<module>``."""
    parts = qualname.split(".")[:-1]
    if parts and parts[-1] == "<locals>":
        parts.pop()
    return ".".join(parts) or MODULE_QUALNAME


class Resolver:
    """Places to symbol IDs on one side of a change, from that side's source."""

    def __init__(
        self,
        names: Mapping[str, str],
        read: Callable[[Sequence[str]], Mapping[str, bytes]],
    ) -> None:
        self.names = names  # path -> module name, for every nameable ``.py`` file
        self.read = read  # paths -> their bytes, for those that exist
        self.tables: dict[str, SymbolTable | None] = {}

    def load(self, paths: Iterable[str]) -> None:
        """Reads and parses the files of ``paths`` not yet loaded, together."""
        wanted = sorted({path for path in paths if path not in self.tables})
        named = [path for path in wanted if path in self.names]
        sources = self.read(named) if named else {}
        for path in wanted:
            source, table = sources.get(path), None
            if source is not None:
                try:
                    table = build_table(source, self.names[path])
                except (SyntaxError, MemoryError, RecursionError, OverflowError):
                    table = None  # C06 reports the file as unanalysed
            self.tables[path] = table

    def table(self, path: str) -> SymbolTable | None:
        self.load([path])
        return self.tables[path]

    def symbol(self, place: Place) -> str | None:
        """The symbol a place belongs to, or None if its file has no symbol table."""
        path, qualname, line = place
        table = self.table(path)
        if table is None:
            return None
        name = qualname
        while True:
            named = [symbol for symbol in table.symbols if symbol.qualname == name]
            if named:
                inside = [symbol for symbol in named if symbol.start <= line <= symbol.end]
                return (inside or named)[0].id
            if name == MODULE_QUALNAME:
                return None
            name = _parent(name)


def side_resolvers(git: Git, base: str, head: str) -> tuple[Resolver, Resolver]:
    """A resolver per side, reading files from the commits as they are needed."""
    files = regular_files(git, base), regular_files(git, head)
    names = module_names(files[0].keys(), files[1].keys())

    def reader(side: Mapping[str, str]) -> Callable[[Sequence[str]], dict[str, bytes]]:
        def read(paths: Sequence[str]) -> dict[str, bytes]:
            shas = {path: side[path] for path in paths if path in side}
            blobs = read_blobs(git, list(shas.values()))
            return {path: blobs[sha] for path, sha in shas.items() if sha in blobs}

        return read

    return Resolver(names[0], reader(files[0])), Resolver(names[1], reader(files[1]))


@dataclass(frozen=True, slots=True)
class TestMap:
    """One side's tests: each test's own symbol, and the symbols each mapped test ran."""

    #: node ID -> the test function's symbol (None: unresolved), in collection order
    tests: Mapping[str, str | None]
    ran: Mapping[str, frozenset[str]]  # node ID -> symbols run, for the tests that were mapped
    unresolved: int = 0  # places that named no symbol
    #: node ID -> why it could not be collected: modules the map knows nothing of, kept so
    #: that a run from the cached map reports them as the run that made it did (H02)
    uncollected: Mapping[str, str] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "format": MAP_FORMAT,
            "tests": dict(self.tests),  # in collection order
            "ran": {test: sorted(symbols) for test, symbols in sorted(self.ran.items())},
            "unresolved": self.unresolved,
            "uncollected": dict(self.uncollected),
        }

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> "TestMap":
        if data.get("format") != MAP_FORMAT:
            raise ValueError("another map format")
        tests = {
            str(test): (str(s) if s is not None else None) for test, s in data["tests"].items()
        }
        ran = {str(test): frozenset(map(str, symbols)) for test, symbols in data["ran"].items()}
        uncollected = {str(test): str(why) for test, why in data["uncollected"].items()}
        return cls(tests, ran, int(data["unresolved"]), uncollected)


def test_map(run: PytestRun, resolver: Resolver) -> TestMap:
    """A run's collected tests and maps, as symbols of ``resolver``'s side."""
    unresolved = 0

    def resolve(place: Place) -> str | None:
        nonlocal unresolved
        symbol = resolver.symbol(place)
        if symbol is None:
            unresolved += 1
        return symbol

    resolver.load(
        place[0]
        for places in (run.collected.values(), *run.maps.values())
        for place in places
        if place is not None
    )
    tests = {test: (resolve(p) if p is not None else None) for test, p in run.collected.items()}
    ran: dict[str, frozenset[str]] = {}
    for test, places in run.maps.items():
        ran[test] = frozenset(s for s in map(resolve, sorted(places)) if s is not None)
        tests.setdefault(test, None)
    uncollected = {error.nodeid: error.message for error in run.collect_errors}
    return TestMap(tests, ran, unresolved, uncollected)


@dataclass(frozen=True, slots=True)
class Selected:
    test: str
    reasons: tuple[SelectionReason, ...]
    symbols: tuple[str, ...]  # the changed symbols, or the test's own, behind the reasons

    def payload(self) -> dict[str, Any]:
        """The ``test.selected`` payload (its envelope's target is ``{"test": test}``)."""
        return {"reasons": list(self.reasons), "symbols": list(self.symbols)}


def base_id(change: SymbolChange) -> str | None:
    """The changed symbol's ID on base (None if the change added it)."""
    if change.change == "added":
        return None
    return change.previous_symbol_id or change.symbol_id


def head_id(change: SymbolChange) -> str | None:
    """The changed symbol's ID on head (None if the change removed it)."""
    return None if change.change == "removed" else change.symbol_id


def _is_module_level(symbol: str) -> bool:
    return f":{MODULE_QUALNAME}#" in symbol


def _at_import(change: SymbolChange) -> list[str]:
    """The IDs of a change whose code runs only at import: a module's own code, or a class whose
    body is all its code. Imports select them, and cover them."""
    ids = [i for i in (base_id(change), head_id(change)) if i]
    if change.body_only or any(_is_module_level(i) for i in ids):
        return ids
    return []


def _test_path(test: str) -> str:
    return test.split("::", 1)[0]


def _under(path: str, conftest: str) -> bool:
    directory = PurePosixPath(conftest).parent
    return directory == PurePosixPath() or PurePosixPath(path).is_relative_to(directory)


def select(
    changes: Sequence[SymbolChange],
    base: TestMap,
    head: TestMap,
    graphs: tuple[ImportGraph, ImportGraph],
) -> tuple[Selected, ...]:
    """The selected tests in node ID order, each with its reasons and the symbols behind them.

    ``base`` is the base side's map (every test run); ``head`` the head side's collection.
    """
    found: dict[str, dict[SelectionReason, set[str]]] = {}

    def add(test: str, reason: SelectionReason, symbols: Iterable[str]) -> None:
        found.setdefault(test, {}).setdefault(reason, set()).update(symbols)

    changed_base = {i for c in changes if (i := base_id(c)) and not _is_module_level(i)}
    bodies = {  # a changed class body, by the prefix of its methods
        i.partition("#")[0] + ".": i
        for c in changes
        if c.kind == "class" and (i := base_id(c)) and not _is_module_level(i)
    }
    for test, ran in base.ran.items():
        touched = ran & changed_base
        touched |= {
            body for prefix, body in bodies.items() if any(r.startswith(prefix) for r in ran)
        }
        if touched:
            add(test, "blast_radius", touched)

    module_level = sorted({i for c in changes for i in _at_import(c)})
    conftests = sorted({c.file for c in changes if PurePosixPath(c.file).name == CONFTEST})
    tests = {**base.tests, **head.tests}
    for symbol in module_level:
        reach = downstream([module_of(symbol)], *graphs, packages=True)
        for test, own in tests.items():
            if own is not None and module_of(own) in reach:
                add(test, "module_import", [symbol])
    for conftest in conftests:  # pytest imports a conftest.py for every test below it
        symbols = [c.symbol_id for c in changes if c.file == conftest]
        for test in tests:
            if _under(_test_path(test), conftest):
                add(test, "module_import", symbols)

    changed_head = {i for c in changes if (i := head_id(c))}
    for test, own in head.tests.items():
        if test not in base.tests:
            add(test, "new_test", [own] if own else [])
        elif own is not None and (own in changed_head or base.tests[test] in changed_base):
            add(test, "modified_test", [own])

    return tuple(
        Selected(
            test,
            tuple(reason for reason in REASONS if reason in reasons),
            tuple(sorted({symbol for symbols in reasons.values() for symbol in symbols})),
        )
        for test, reasons in sorted(found.items())
    )


def test_modules(*maps: TestMap) -> set[str]:
    """The modules tests are collected from, on either side."""
    return {module_of(own) for m in maps for own in m.tests.values() if own is not None}


def uncovered(
    changes: Sequence[SymbolChange],
    selected: Sequence[Selected],
    base: TestMap,
    head: TestMap,
) -> tuple[SymbolChange, ...]:
    """The changed symbols no selected test covers (see the module's docstring).

    ``base`` must hold the base map; ``head`` the map of the selected tests run on head.
    """
    chosen = {s.test for s in selected}
    ran = {
        symbol
        for side in (base, head)
        for test, symbols in side.ran.items()
        if test in chosen
        for symbol in symbols
    }
    imported = {symbol for s in selected if "module_import" in s.reasons for symbol in s.symbols}
    own_tests = test_modules(base, head)
    gaps = []
    for change in changes:
        ids = [i for i in (base_id(change), head_id(change)) if i is not None]
        if PurePosixPath(change.file).name == CONFTEST or module_of(ids[0]) in own_tests:
            continue  # test code
        if _at_import(change):
            covered = any(i in imported for i in ids) or any(i in ran for i in ids)
        elif change.kind == "class":
            prefixes = tuple(i.partition("#")[0] + "." for i in ids)
            covered = any(i in ran for i in ids) or any(
                symbol.startswith(prefixes) for symbol in ran
            )
        else:
            covered = any(i in ran for i in ids)
        if not covered:
            gaps.append(change)
    return tuple(gaps)


def no_covering_test(change: SymbolChange) -> dict[str, Any]:
    """The ``missing.recorded`` payload for a changed symbol no selected test runs."""
    return {
        "missing": {
            "reason": "no_covering_test",
            "targets": [{"file": change.file, "symbol": change.symbol_id}],
            "detail": f"no selected test runs {change.symbol_id} ({change.change})"[:1000],
            "resolution": "Add or point to a test that exercises it",
        }
    }


def map_key(commit: str, lock_sha256: str | None, python: str, platform: str) -> str:
    """What a base map depends on: the commit, the locked dependencies, the interpreter and the
    platform it ran on, and the map's format."""
    text = f"{MAP_FORMAT}\n{commit}\n{lock_sha256 or '-'}\n{python}\n{platform}\n"
    return hashlib.sha256(text.encode()).hexdigest()


class MapCache:
    """Base maps on disk, one JSON file per key; a broken file is a miss."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, key: str) -> TestMap | None:
        try:
            data = json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))
            return TestMap.from_payload(data)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def put(self, key: str, value: TestMap) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        partial = self.root / f".{key}.{uuid.uuid4().hex[:8]}.tmp"
        partial.write_bytes(json.dumps(value.payload(), sort_keys=True).encode())
        partial.replace(self.root / f"{key}.json")
