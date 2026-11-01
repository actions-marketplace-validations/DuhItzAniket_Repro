"""Changes to the tests themselves (D06, ARCHITECTURE §4.2): removed tests, new skips and
xfails, removed or weakened assertions, widened ``pytest.raises``.

Agents are known to edit or delete tests to make them pass (ImpossibleBench, research/02 §1.9),
so the tests a change touches are compared with their base version, from the source, never run:

- ``test_removed``: a test collected on base is not collected on head;
- ``skip_added`` / ``xfail_added``: a skip or xfail marker, or a ``pytest.skip`` /
  ``pytest.xfail`` call, that base did not have;
- ``assertion_removed``: fewer assertions (``assert`` statements and unittest ``assert*``
  calls) than on base;
- ``assertion_weakened``: as many, but weaker: an equality or identity check became a looser
  comparison, or a mere truth test;
- ``raises_widened``: a ``pytest.raises`` now accepts ``Exception`` or ``BaseException``, or
  lost its ``match``;
- ``test_modified``: the test changed in none of those ways;
- ``base_test_fails_on_head``: the base version of an edited test, run against head's code,
  fails where it passed on base (found by running, ``repro.runner.twosided``).

**A parametrized test's cases.** Adding a case to ``@pytest.mark.parametrize`` changes the
test's code, but not its other cases: each runs the same code with the same parameters as on
base. A case is **kept** (``kept_cases``) when its test's code differs only in its
``parametrize`` decorators' cases (their ``argvalues`` and ``ids``) and the case has the same
ID and the same parameters and marks on both sides (the plugin's digest of plain values,
``repro_pytest._case``). A kept case is not ``modified``: its outcomes are compared as any
other test's. A case whose parameters changed, or could not be read, stays ``modified``, and
the test's ``test_modified`` detail counts each kind.
"""

import ast
import copy
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from repro import __version__
from repro.analysis.changes import SymbolChange
from repro.ids import new_evidence_id
from repro.runner.selection import Resolver, TestMap, base_id, head_id

TestChangeKind = Literal[
    "test_removed",
    "skip_added",
    "xfail_added",
    "assertion_removed",
    "assertion_weakened",
    "raises_widened",
    "test_modified",
    "base_test_fails_on_head",
]
_BROAD: Final = frozenset({"Exception", "BaseException"})
_EXACT_COMPARE: Final = (ast.Eq, ast.Is)
_EXACT_UNITTEST: Final = frozenset(
    {
        "assertEqual",
        "assertEquals",
        "assertIs",
        "assertListEqual",
        "assertDictEqual",
        "assertTupleEqual",
        "assertSetEqual",
        "assertSequenceEqual",
        "assertMultiLineEqual",
        "assertCountEqual",
        "assertAlmostEqual",
    }
)
_TRUTH_UNITTEST: Final = frozenset(
    {"assertTrue", "assertFalse", "assertIsNotNone", "assertIsNone", "assert_"}
)


@dataclass(frozen=True, slots=True)
class TestChange:
    file: str
    test: str | None
    change: TestChangeKind
    detail: str

    @property
    def status(self) -> str:
        """The ``diff.tests`` envelope status (ADR-0015 §2): ``warn`` for a test that only
        changed, ``fail`` for every finding that is a problem on its own."""
        return "warn" if self.change == "test_modified" else "fail"

    def evidence(self, run_id: str) -> dict[str, Any]:
        """The ``test_change`` evidence record (``evidence.created``; the writer adds ``seq``)."""
        target: dict[str, str] = {"file": self.file}
        if self.test is not None:
            target["test"] = self.test
        return {
            "id": new_evidence_id(),
            "run_id": run_id,
            "type": "test_change",
            "targets": [target],
            "summary": f"{self.change}: {self.test or self.file}: {self.detail}"[:280],
            "data": {"change": self.change, "detail": self.detail},
            "artifacts": [],
            "stability": "n/a",
            "origin": "introduced",
            "produced_by": {"component": "repro.runner.testdiff", "version": __version__},
        }

    def payload(self, evidence_id: str) -> dict[str, Any]:
        """The ``diff.tests`` payload."""
        return {
            "evidence_id": evidence_id,
            "file": self.file,
            "test": self.test,
            "change": self.change,
            "detail": self.detail[:1000],
        }


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _marker(name: str) -> str | None:
    last = name.rsplit(".", 1)[-1]
    if last in ("skip", "skipif", "skipIf", "skipUnless"):
        return "skip"
    if last in ("xfail", "expectedFailure"):
        return "xfail"
    return None


def _own_nodes(function: ast.AST) -> Iterable[ast.AST]:
    """The function's nodes, without those of functions or classes nested in it."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


@dataclass(frozen=True, slots=True)
class _Shape:
    """What decides a test's strength."""

    markers: frozenset[str]  # "skip", "xfail": decorators and pytest.skip/xfail calls
    assertions: tuple[int, ...]  # each assertion's strength: 2 exact, 1 comparison, 0 truth
    raises: tuple[tuple[bool, bool], ...]  # each pytest.raises: (broad, has match)


def _strength(node: ast.AST) -> int:
    if isinstance(node, ast.Assert):
        test = node.test
        if isinstance(test, ast.Compare):
            return 2 if all(isinstance(op, _EXACT_COMPARE) for op in test.ops) else 1
        return 0
    name = _dotted(node.func).rsplit(".", 1)[-1] if isinstance(node, ast.Call) else ""
    if name in _EXACT_UNITTEST:
        return 2
    return 0 if name in _TRUTH_UNITTEST else 1


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr.startswith("assert") and _dotted(node.func.value) == "self"
    return False


def _raised(node: ast.Call) -> tuple[bool, bool]:
    names: list[str] = []
    if node.args:
        first = node.args[0]
        items = first.elts if isinstance(first, ast.Tuple) else [first]
        names = [_dotted(item).rsplit(".", 1)[-1] for item in items]
    has_match = any(keyword.arg == "match" for keyword in node.keywords)
    return any(name in _BROAD for name in names), has_match


def shape(function: ast.AST) -> _Shape:
    markers: set[str] = set()
    if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        markers.update(m for d in function.decorator_list if (m := _marker(_dotted(d))))
    assertions: list[tuple[int, int, int]] = []
    raises: list[tuple[int, int, tuple[bool, bool]]] = []
    for node in _own_nodes(function):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in ("pytest.skip", "pytest.xfail", "skip", "xfail"):
                markers.add("skip" if name.endswith("skip") else "xfail")
            if name in ("pytest.raises", "raises"):
                raises.append((node.lineno, node.col_offset, _raised(node)))
        if _is_assertion(node):
            where = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
            assertions.append((*where, _strength(node)))
    return _Shape(
        frozenset(markers),
        tuple(s for *_, s in sorted(assertions)),
        tuple(r for *_, r in sorted(raises)),
    )


def compare(base: _Shape, head: _Shape) -> list[tuple[TestChangeKind, str]]:
    """How head's version of a test is weaker than base's, each with a short detail."""
    found: list[tuple[TestChangeKind, str]] = []
    if "skip" in head.markers - base.markers:
        found.append(("skip_added", "a skip was added"))
    if "xfail" in head.markers - base.markers:
        found.append(("xfail_added", "an xfail was added"))
    if len(head.assertions) < len(base.assertions):
        found.append(
            (
                "assertion_removed",
                f"{len(base.assertions)} assertions on base, {len(head.assertions)} on head",
            )
        )
    elif sum(head.assertions) < sum(base.assertions):
        found.append(("assertion_weakened", "an exact check became a looser one"))
    widened = any(
        (h_broad and not b_broad) or (b_match and not h_match)
        for (b_broad, b_match), (h_broad, h_match) in zip(base.raises, head.raises, strict=False)
    )
    if widened:
        found.append(("raises_widened", "pytest.raises accepts more than on base"))
    return found


#: A ``parametrize`` call's arguments that hold its cases, by position and by keyword.
_CASE_ARGUMENTS: Final = {1: "argvalues", 3: "ids"}
_CODE_CHANGED: Final = "the test's code changed"


def _is_parametrize(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and _dotted(decorator.func).rsplit(".", 1)[-1] == "parametrize"
    )


def parametrized(function: ast.AST) -> bool:
    """Whether a test function has a ``parametrize`` decorator of its own."""
    decorators = getattr(function, "decorator_list", [])
    return isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
        _is_parametrize(d) for d in decorators
    )


def _without_cases(decorator: ast.expr) -> ast.expr:
    """A ``parametrize`` decorator with its cases left out (each replaced by ``None``, so that
    the other arguments keep their places); any other decorator as it is."""
    if not isinstance(decorator, ast.Call) or not _is_parametrize(decorator):
        return decorator
    args: list[ast.expr] = [
        ast.Constant(None) if index in _CASE_ARGUMENTS else arg
        for index, arg in enumerate(decorator.args)
    ]
    cases = set(_CASE_ARGUMENTS.values())
    keywords = [k for k in decorator.keywords if k.arg not in cases]
    return ast.Call(decorator.func, args, keywords)


def common_code(function: ast.AST) -> str:
    """What a test's cases have in common: its code as ``ast.dump`` gives it (no positions),
    with each ``parametrize`` decorator's cases left out."""
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ast.dump(function)
    common = copy.copy(function)  # shallow: the resolver's own tree is left as it is
    common.decorator_list = [_without_cases(d) for d in function.decorator_list]
    return ast.dump(common)


@dataclass(frozen=True, slots=True)
class _Edited:
    """A test function the change edited, on both sides."""

    file: str
    old: str  # its symbol on base
    new: str  # its symbol on head
    old_node: ast.AST
    new_node: ast.AST

    @property
    def cases_only(self) -> bool:
        """Whether it is parametrized on both sides and only its ``parametrize`` cases
        changed."""
        return (
            parametrized(self.old_node)
            and parametrized(self.new_node)
            and common_code(self.old_node) == common_code(self.new_node)
        )


def _edited(
    changes: Sequence[SymbolChange],
    base: TestMap,
    head: TestMap,
    resolvers: tuple[Resolver, Resolver],
) -> Iterator[_Edited]:
    """The test functions the change edited, on both sides, in the changes' order."""
    base_tests = {own for own in base.tests.values() if own is not None}
    head_tests = {own for own in head.tests.values() if own is not None}
    for change in changes:
        old, new = base_id(change), head_id(change)
        if old is None or new is None or old not in base_tests or new not in head_tests:
            continue  # not a test on both sides (a new test, or a removed one, is reported apart)
        old_node, new_node = _function(resolvers[0], old), _function(resolvers[1], new)
        if old_node is not None and new_node is not None:
            yield _Edited(change.file, old, new, old_node, new_node)


@dataclass(frozen=True, slots=True)
class _Cases:
    """An edited test's cases, by what happened to them, in collection order."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    kept: tuple[str, ...]  # the same parameters on both sides
    changed: tuple[str, ...]  # other parameters on head
    unread: tuple[str, ...]  # parameters that are not plain values, on either side

    def detail(self) -> str:
        counts = [
            f"{len(self.added)} added",
            f"{len(self.removed)} removed",
            f"{len(self.kept)} kept their parameters",
        ]
        if self.changed:
            counts.append(f"{len(self.changed)} changed theirs")
        if self.unread:
            counts.append(f"{len(self.unread)} could not be compared")
        return f"only its parametrize cases changed: {', '.join(counts)}"


def _cases(edited: _Edited, base: TestMap, head: TestMap) -> _Cases:
    on_base = [t for t, own in base.tests.items() if own == edited.old]
    on_head = [t for t, own in head.tests.items() if own == edited.new]
    base_set, head_set = set(on_base), set(on_head)
    kept: list[str] = []
    changed: list[str] = []
    unread: list[str] = []
    for test in (t for t in on_head if t in base_set):
        old, new = base.cases.get(test), head.cases.get(test)
        if old is None or new is None:
            unread.append(test)
        else:
            (kept if old == new else changed).append(test)
    return _Cases(
        tuple(t for t in on_head if t not in base_set),
        tuple(t for t in on_base if t not in head_set),
        tuple(kept),
        tuple(changed),
        tuple(unread),
    )


def kept_cases(
    changes: Sequence[SymbolChange],
    base: TestMap,
    head: TestMap,
    resolvers: tuple[Resolver, Resolver],
) -> frozenset[str]:
    """The cases of edited parametrized tests that the change left as they were (the module's
    docstring): they are not ``modified``."""
    return frozenset(
        test
        for edited in _edited(changes, base, head, resolvers)
        if edited.cases_only
        for test in _cases(edited, base, head).kept
    )


def _test_ids(tests: Mapping[str, str | None], symbol: str) -> list[str]:
    """The node IDs of a test function, without parameters, in order."""
    ids = {test.split("[", 1)[0] for test, own in tests.items() if own == symbol}
    return sorted(ids)


def _function(resolver: Resolver, symbol: str) -> ast.AST | None:
    module = symbol.partition(":")[0]
    paths = [path for path, name in resolver.names.items() if name == module]
    table = resolver.table(paths[0]) if paths else None
    if table is None:
        return None
    return next((s.node for s in table.symbols if s.id == symbol), None)


def test_changes(
    changes: Sequence[SymbolChange],
    base: TestMap,
    head: TestMap,
    resolvers: tuple[Resolver, Resolver],
) -> list[TestChange]:
    """The test-file diff: removed tests, then each changed test's findings, in order."""
    found = [
        TestChange(test.split("::", 1)[0], test, "test_removed", "not collected on head")
        for test in sorted(set(base.tests) - set(head.tests))
    ]
    for edited in _edited(changes, base, head, resolvers):
        edit = _cases(edited, base, head).detail() if edited.cases_only else _CODE_CHANGED
        findings = compare(shape(edited.old_node), shape(edited.new_node)) or [
            ("test_modified", edit)
        ]
        tests: list[str | None] = [*_test_ids(head.tests, edited.new)] or [None]
        for test in tests:
            found.extend(TestChange(edited.file, test, kind, detail) for kind, detail in findings)
    return found
