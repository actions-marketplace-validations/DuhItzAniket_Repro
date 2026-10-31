"""Test selection (D05): places to symbols, the selection rule, coverage gaps, the map cache.

The rule is tested on synthetic maps here; ``test_selection_fixtures.py`` runs it for real on
the fixture repositories. Names from ``repro.runner.selection`` are used through the module, so
pytest does not mistake ``test_map`` or ``TestMap`` for tests.
"""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from repro.analysis.blast import downstream
from repro.analysis.changes import SymbolChange
from repro.analysis.imports import ImportGraph
from repro.runner import selection
from repro.runner.pytest import read_results
from repro_events import models

SOURCE = b'''"""A module."""

LIMIT = 3


def plain():
    return [x for x in range(LIMIT)]


@decorated
def wrapped():
    return lambda: 1


class Box:
    @property
    def size(self):
        return 1

    @size.setter
    def size(self, value):
        pass

    def outer(self):
        def inner():
            return 2

        return inner
'''


def resolver(files: dict[str, bytes] | None = None) -> selection.Resolver:
    files = files if files is not None else {"pkg/mod.py": SOURCE}
    names = {path: path[: -len(".py")].replace("/", ".") for path in files}
    reads: list[list[str]] = []

    def read(paths: Sequence[str]) -> dict[str, bytes]:
        reads.append(list(paths))
        return {path: files[path] for path in paths if path in files}

    found = selection.Resolver(names, read)
    found.reads = reads  # type: ignore[attr-defined]
    return found


@pytest.mark.parametrize(
    ("qualname", "line", "symbol"),
    [
        ("plain", 6, "pkg.mod:plain#0"),
        ("plain.<locals>.<listcomp>", 7, "pkg.mod:plain#0"),  # a comprehension's own code
        ("wrapped", 10, "pkg.mod:wrapped#0"),  # a decorated function starts at its decorator
        ("wrapped.<locals>.<lambda>", 12, "pkg.mod:wrapped#0"),
        ("Box", 15, "pkg.mod:Box#0"),
        ("Box.size", 16, "pkg.mod:Box.size#0"),  # the getter
        ("Box.size", 20, "pkg.mod:Box.size#1"),  # the setter: same name, told apart by line
        ("Box.outer.<locals>.inner", 25, "pkg.mod:Box.outer.<locals>.inner#0"),
        ("Box.__annotate__", 15, "pkg.mod:Box#0"),  # no symbol of its own: the enclosing one
        ("<module>", 1, "pkg.mod:<module>#0"),
    ],
)
def test_places_resolve_to_symbols(qualname: str, line: int, symbol: str) -> None:
    assert resolver().symbol(("pkg/mod.py", qualname, line)) == symbol


def test_places_without_a_table() -> None:
    found = resolver({"pkg/mod.py": SOURCE, "pkg/broken.py": b"def (:\n"})
    assert found.symbol(("pkg/missing.py", "f", 1)) is None
    assert found.symbol(("pkg/broken.py", "f", 1)) is None
    assert found.symbol(("pkg/mod.py", "no_such_name", 1)) == "pkg.mod:<module>#0"


def test_a_run_becomes_a_map_and_files_are_read_together() -> None:
    def record(**fields: object) -> bytes:
        return json.dumps(fields).encode() + b"\n"

    data = (
        record(kind="collected", nodeid="t.py::test_a", test=["t.py", "test_a", 1])
        + record(kind="collected", nodeid="t.py::doctest", test=None)
        + record(
            kind="map",
            nodeid="t.py::test_a",
            functions=[["t.py", "test_a", 1], ["pkg/mod.py", "plain", 6], ["x.py", "f", 1]],
        )
        + record(kind="session", exitstatus=0, collected=2)
    )
    found = resolver({"pkg/mod.py": SOURCE, "t.py": b"def test_a():\n    pass\n"})
    built = selection.test_map(read_results(data), found)
    assert built.tests == {"t.py::test_a": "t:test_a#0", "t.py::doctest": None}
    assert built.ran == {"t.py::test_a": frozenset({"t:test_a#0", "pkg.mod:plain#0"})}
    assert built.unresolved == 1  # x.py is not a file of the snapshot
    assert found.reads == [["pkg/mod.py", "t.py"]]  # type: ignore[attr-defined]


def change(symbol: str, kind: str = "modified", **fields: object) -> SymbolChange:
    module = symbol.partition(":")[0]
    return SymbolChange(
        symbol_id=symbol,
        change=kind,  # type: ignore[arg-type]
        kind=fields.pop("symbol_kind", "function"),  # type: ignore[arg-type]
        file=str(fields.pop("file", module.replace(".", "/") + ".py")),
        base_lines=None,
        head_lines=None,
        previous_symbol_id=fields.pop("previous", None),  # type: ignore[arg-type]
        body_only=bool(fields.pop("body_only", False)),
    )


def graph(*imports: tuple[str, str], modules: Sequence[str] = ()) -> ImportGraph:
    return ImportGraph(dict.fromkeys(modules, ""), frozenset(imports), ())


NO_IMPORTS = (graph(), graph())


def test_tests_that_ran_a_changed_function_are_selected() -> None:
    base = selection.TestMap(
        {"t.py::a": "t:a#0", "t.py::b": "t:b#0", "t.py::c": "t:c#0"},
        {
            "t.py::a": frozenset({"t:a#0", "pkg.m:f#0", "pkg.m:g#0"}),
            "t.py::b": frozenset({"t:b#0", "pkg.m:h#0"}),
            "t.py::c": frozenset({"t:c#0", "pkg.m:old#0"}),
        },
    )
    head = selection.TestMap(dict(base.tests), {})
    changes = [
        change("pkg.m:f#0"),
        change("pkg.m:g#0"),
        change("pkg.m:new#0", "renamed", previous="pkg.m:old#0"),
        change("pkg.m:added#0", "added"),
    ]
    chosen = selection.select(changes, base, head, NO_IMPORTS)
    assert [(s.test, s.reasons, s.symbols) for s in chosen] == [
        ("t.py::a", ("blast_radius",), ("pkg.m:f#0", "pkg.m:g#0")),
        ("t.py::c", ("blast_radius",), ("pkg.m:old#0",)),  # by its name on base
    ]
    models.TestSelectedPayload.model_validate(chosen[0].payload())


def test_a_changed_class_body_selects_the_tests_that_ran_its_methods() -> None:
    """H06: a class attribute changes what every method sees; it runs at import, not in a test."""
    base = selection.TestMap(
        {"t.py::a": "t:a#0", "t.py::b": "t:b#0", "t.py::c": "t:c#0"},
        {
            "t.py::a": frozenset({"t:a#0", "pkg.m:C.sign#0"}),
            "t.py::b": frozenset({"t:b#0", "pkg.m:CC.sign#0"}),  # another class, same start
            "t.py::c": frozenset({"t:c#0", "pkg.m:f#0"}),
        },
    )
    head = selection.TestMap(dict(base.tests), {})
    chosen = selection.select([change("pkg.m:C#0", symbol_kind="class")], base, head, NO_IMPORTS)
    assert [(s.test, s.reasons, s.symbols) for s in chosen] == [
        ("t.py::a", ("blast_radius",), ("pkg.m:C#0",)),
    ]


def test_module_level_changes_select_the_tests_that_import_them() -> None:
    tests = {"tests/test_a.py::t": "test_a:t#0", "tests/test_b.py::t": "test_b:t#0"}
    base = selection.TestMap(tests, {test: frozenset() for test in tests})
    head = selection.TestMap(dict(tests), {})
    graphs = (graph(("pkg.api", "pkg.core"), ("test_a", "pkg.api")), graph())
    chosen = selection.select([change("pkg.core:<module>#0")], base, head, graphs)
    assert [(s.test, s.reasons, s.symbols) for s in chosen] == [
        ("tests/test_a.py::t", ("module_import",), ("pkg.core:<module>#0",))
    ]


def test_a_changed_conftest_selects_the_tests_below_it() -> None:
    tests = {"tests/unit/test_a.py::t": "test_a:t#0", "other/test_b.py::t": "test_b:t#0"}
    base = selection.TestMap(tests, {test: frozenset() for test in tests})
    changes = [change("conftest:<module>#0", file="tests/conftest.py")]
    chosen = selection.select(changes, base, selection.TestMap(dict(tests), {}), NO_IMPORTS)
    assert [s.test for s in chosen] == ["tests/unit/test_a.py::t"]
    assert chosen[0].reasons == ("module_import",)


def test_new_and_modified_tests_are_selected() -> None:
    base = selection.TestMap({"t.py::a": "t:a#0", "t.py::b": "t:b#0"}, {})
    head = selection.TestMap(
        {"t.py::a": "t:a#0", "t.py::b": "t:b#0", "t.py::c": "t:c#0", "t.py::b[2]": "t:b#0"}, {}
    )
    chosen = selection.select([change("t:a#0"), change("t:c#0", "added")], base, head, NO_IMPORTS)
    assert [(s.test, s.reasons) for s in chosen] == [
        ("t.py::a", ("modified_test",)),
        ("t.py::b[2]", ("new_test",)),  # a new parameter of an unchanged test
        ("t.py::c", ("new_test",)),
    ]


def test_coverage_gaps() -> None:
    base = selection.TestMap(
        {"tests/test_m.py::a": "test_m:a#0"},
        {"tests/test_m.py::a": frozenset({"test_m:a#0", "pkg.m:f#0", "pkg.m:C.m#0"})},
    )
    head = selection.TestMap(
        {"tests/test_m.py::a": "test_m:a#0"},
        {"tests/test_m.py::a": frozenset({"test_m:a#0", "pkg.m:f#0", "pkg.m:fresh#0"})},
    )
    changes = [
        change("pkg.m:f#0"),  # run on both sides
        change("pkg.m:gone#0", "removed"),  # no test ran it on base
        change("pkg.m:fresh#0", "added"),  # a selected test runs it on head
        change("pkg.m:lonely#0", "added"),
        change("pkg.m:C#0", symbol_kind="class"),  # its method ran
        change("pkg.m:D#0", symbol_kind="class"),
        change("pkg.m:<module>#0"),  # no selected test imports it
        change("test_m:helper#0", file="tests/test_m.py"),  # test code
        change("conftest:fixture#0", file="tests/conftest.py"),  # test code
    ]
    chosen = selection.select(changes, base, head, NO_IMPORTS)
    gaps = selection.uncovered(changes, chosen, base, head)
    assert [gap.symbol_id for gap in gaps] == [
        "pkg.m:gone#0",
        "pkg.m:lonely#0",
        "pkg.m:D#0",
        "pkg.m:<module>#0",
    ]
    item = selection.no_covering_test(gaps[0])
    models.MissingRecordedPayload.model_validate(item)
    assert item["missing"]["targets"] == [{"file": "pkg/m.py", "symbol": "pkg.m:gone#0"}]


def test_a_module_level_change_is_covered_by_a_test_that_imports_it() -> None:
    tests = {"tests/test_a.py::t": "test_a:t#0"}
    base = selection.TestMap(tests, {"tests/test_a.py::t": frozenset({"test_a:t#0"})})
    head = selection.TestMap(dict(tests), {})
    changes = [change("pkg.core:<module>#0")]
    graphs = (graph(("test_a", "pkg.core")), graph())
    chosen = selection.select(changes, base, head, graphs)
    assert selection.uncovered(changes, chosen, base, head) == ()


PACKAGES = ("pkg", "pkg.text", "pkg.text.squash", "pkg.other", "test_a", "test_b")


def test_a_package_runs_before_its_modules() -> None:
    """``from pkg.text import squash`` runs ``pkg/text/__init__.py`` (and ``pkg``'s) first, though
    the import graphs hold only the edge to ``pkg.text.squash``."""
    tests = {"tests/test_a.py::t": "test_a:t#0", "tests/test_b.py::t": "test_b:t#0"}
    base = selection.TestMap(tests, {test: frozenset() for test in tests})
    head = selection.TestMap(dict(tests), {})
    graphs = (
        graph(("test_a", "pkg.text.squash"), ("test_b", "pkg.other"), modules=PACKAGES),
        graph(modules=PACKAGES),
    )
    changes = [change("pkg.text:<module>#0", "added", file="pkg/text/__init__.py")]
    chosen = selection.select(changes, base, head, graphs)
    assert [(s.test, s.reasons, s.symbols) for s in chosen] == [
        ("tests/test_a.py::t", ("module_import",), ("pkg.text:<module>#0",)),
    ]
    assert selection.uncovered(changes, chosen, base, head) == ()
    # pkg/__init__.py runs before every module of the package, pkg.other's included.
    chosen = selection.select(
        [change("pkg:<module>#0", file="pkg/__init__.py")], base, head, graphs
    )
    assert [s.test for s in chosen] == ["tests/test_a.py::t", "tests/test_b.py::t"]
    # The blast radius shown keeps the static imports alone.
    assert downstream(["pkg.text"], *graphs) == {"pkg.text": 0}
    assert downstream(["pkg.text"], *graphs, packages=True) == {
        "pkg.text": 0,
        "pkg.text.squash": 1,
        "test_a": 2,
    }


def test_a_class_that_is_only_a_body_is_selected_and_covered_by_imports() -> None:
    """An enum's members or an exception class: nothing of it runs in a test, its body runs at
    import, as a module's own code does. A class with code to run must have run it."""
    tests = {"tests/test_a.py::t": "test_a:t#0"}
    base = selection.TestMap(tests, {"tests/test_a.py::t": frozenset({"test_a:t#0"})})
    head = selection.TestMap(dict(tests), {"tests/test_a.py::t": frozenset({"test_a:t#0"})})
    graphs = (graph(("test_a", "pkg.m"), modules=("pkg.m", "test_a")), graph())
    kind = change("pkg.m:Kind#0", symbol_kind="class", body_only=True)
    chosen = selection.select([kind], base, head, graphs)
    assert [(s.test, s.reasons, s.symbols) for s in chosen] == [
        ("tests/test_a.py::t", ("module_import",), ("pkg.m:Kind#0",)),
    ]
    assert selection.uncovered([kind], chosen, base, head) == ()
    record = change("pkg.m:Record#0", symbol_kind="class")  # a dataclass nothing constructs
    chosen = selection.select([record], base, head, graphs)
    assert chosen == ()
    assert selection.uncovered([record], chosen, base, head) == (record,)


def test_a_class_is_covered_when_a_method_written_for_it_ran() -> None:
    """The plugin maps a dataclass's written ``__init__`` as the class itself."""
    tests = {"tests/test_a.py::t": "test_a:t#0"}
    base = selection.TestMap(tests, {"tests/test_a.py::t": frozenset({"test_a:t#0"})})
    head = selection.TestMap(
        dict(tests), {"tests/test_a.py::t": frozenset({"test_a:t#0", "pkg.m:Record#0"})}
    )
    record = change("pkg.m:Record#0", "added", symbol_kind="class")
    chosen = (selection.Selected("tests/test_a.py::t", ("new_test",), ()),)
    assert selection.uncovered([record], chosen, base, head) == ()


def test_the_map_cache(tmp_path: Path) -> None:
    cache = selection.MapCache(tmp_path / "maps")
    stored = selection.TestMap(
        {"t.py::a": "t:a#0", "t.py::d": None}, {"t.py::a": frozenset({"t:a#0"})}, 2
    )
    key = selection.map_key("a" * 40, "f" * 64, "3.13.13", "win32")
    assert cache.get(key) is None
    cache.put(key, stored)
    assert cache.get(key) == stored
    (tmp_path / "maps" / f"{key}.json").write_text("{broken", encoding="utf-8")
    assert cache.get(key) is None
    other = {**stored.payload(), "format": selection.MAP_FORMAT + 1}
    (tmp_path / "maps" / f"{key}.json").write_text(json.dumps(other), encoding="utf-8")
    assert cache.get(key) is None


def test_the_map_key_depends_on_everything_the_map_does() -> None:
    parts = ("a" * 40, "f" * 64, "3.13.13", "win32")
    key = selection.map_key(*parts)
    for index in range(len(parts)):
        changed = list(parts)
        changed[index] = changed[index] + "x"
        assert selection.map_key(*changed) != key
    assert selection.map_key("a" * 40, None, "3.13.13", "win32") != key
