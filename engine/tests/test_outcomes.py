"""Outcome classification and the test-file diff (D06), on their own."""

import ast
from collections.abc import Mapping
from typing import Any

import pytest

from repro.analysis.changes import SymbolChange
from repro.ids import new_run_id
from repro.runner import outcomes, selection, testdiff
from repro_events import models

RUN = new_run_id()


def diff(**runs: Any) -> outcomes.OutcomeDiff:
    options = {"base": "passed", "base_again": "passed", "head": "passed", "modified": False}
    return outcomes.classify("t.py::t", **{**options, **runs})


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ({"base": None, "base_again": None}, "new"),
        ({"head": None}, "removed"),
        ({"base_again": "failed"}, "flaky"),  # A and A2 disagree
        ({"modified": True, "head": "failed"}, "modified"),
        ({}, "P2P"),
        ({"base": "xfailed", "base_again": "xfailed", "head": "xpassed"}, "P2P"),
        ({"base": "failed", "base_again": "error", "head": "error"}, "F2F"),
        ({"base": "skipped", "base_again": "skipped", "head": "skipped"}, "F2F"),
    ],
)
def test_classes_without_a_flip(runs: dict[str, Any], expected: str) -> None:
    assert diff(**runs).outcome_class == expected


def test_a_flip_needs_every_rerun_to_repeat_it() -> None:
    fixed = {"base": "failed", "base_again": "failed", "head": "passed"}
    assert outcomes.is_flip("failed", "failed", "passed", modified=False)
    assert diff(**fixed).outcome_class == "flaky"  # not yet run again
    confirmed = diff(**fixed, base_reruns=("failed",) * 3, head_reruns=("passed",) * 3)
    assert confirmed.outcome_class == "F2P"
    assert confirmed.stable
    wobbly = diff(**fixed, base_reruns=("failed",) * 3, head_reruns=("passed", "failed", "passed"))
    assert wobbly.outcome_class == "flaky"
    assert not wobbly.stable


def test_a_skip_on_head_does_not_pass() -> None:
    skipped = {"base": "passed", "base_again": "passed", "head": "skipped"}
    found = diff(**skipped, base_reruns=("passed",) * 3, head_reruns=("skipped",) * 3)
    assert found.outcome_class == "P2F"


def test_deflakers_mark_stays_on_the_evidence() -> None:
    found = diff(
        base="passed",
        head="failed",
        base_reruns=("passed",) * 3,
        head_reruns=("failed",) * 3,
        ran_changed_code=False,
    )
    assert (found.outcome_class, found.likely_flaky) == ("P2F", True)
    assert "ran none of the changed code" in found.summary()
    assert found.evidence(RUN)["data"]["likely_flaky"] is True


@pytest.mark.parametrize(
    ("kind", "origin"),
    [("P2P", "n/a"), ("F2F", "pre-existing"), ("new", "introduced"), ("flaky", "n/a")],
)
def test_evidence_and_payload_follow_the_schema(kind: str, origin: str) -> None:
    runs: dict[str, dict[str, Any]] = {
        "P2P": {},
        "F2F": {"base": "failed", "base_again": "failed", "head": "failed"},
        "new": {"base": None, "base_again": None},
        "flaky": {"base_again": "failed"},
    }
    found = diff(**runs[kind])
    record = found.evidence(RUN)
    assert (record["origin"], record["stability"]) == (
        origin,
        "unstable" if kind == "flaky" else "stable",
    )
    models.Evidence.model_validate({**record, "seq": 1})
    models.DiffOutcomePayload.model_validate(found.payload(record["id"]))
    if kind == "flaky":
        models.MissingRecordedPayload.model_validate(outcomes.unstable_observation(found))


def shape(source: str) -> Any:
    return testdiff.shape(ast.parse(source).body[0])


BASE_TEST = """
def test_it():
    assert f(1) == 2
    assert f(2) is None
    with pytest.raises(ValueError, match="bad"):
        f(-1)
"""


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (BASE_TEST.replace("def test_it", "@pytest.mark.skip\ndef test_it"), ["skip_added"]),
        (
            BASE_TEST.replace("def test_it", "@pytest.mark.skipif(True, reason='x')\ndef test_it"),
            ["skip_added"],
        ),
        (
            BASE_TEST.replace(
                "    assert f(1) == 2\n", "    pytest.skip('later')\n    assert f(1) == 2\n"
            ),
            ["skip_added"],
        ),
        (BASE_TEST.replace("def test_it", "@pytest.mark.xfail\ndef test_it"), ["xfail_added"]),
        (BASE_TEST.replace("    assert f(2) is None\n", ""), ["assertion_removed"]),
        (BASE_TEST.replace("assert f(1) == 2", "assert f(1)"), ["assertion_weakened"]),
        (BASE_TEST.replace("assert f(1) == 2", "assert f(1) > 0"), ["assertion_weakened"]),
        (BASE_TEST.replace("ValueError", "Exception"), ["raises_widened"]),
        (BASE_TEST.replace(', match="bad"', ""), ["raises_widened"]),
        (BASE_TEST.replace("f(-1)", "f(-2)"), []),  # changed, not weakened: test_modified
        (BASE_TEST.replace("assert f(1) == 2", "assert f(1) == 2\n    assert g() == 1"), []),
    ],
)
def test_weaker_tests_are_named(head: str, expected: list[str]) -> None:
    assert [kind for kind, _ in testdiff.compare(shape(BASE_TEST), shape(head))] == expected


def test_assertions_of_nested_functions_are_not_the_tests() -> None:
    nested = "def test_it():\n    def helper():\n        assert x == 1\n    helper()\n"
    assert shape(nested).assertions == ()
    unittest = (
        "def test_it(self):\n    self.assertEqual(a, 1)\n    self.assertTrue(b)\n"
        "    self.assertIn(c, d)\n"
    )
    assert shape(unittest).assertions == (2, 0, 1)


def test_the_test_file_diff_of_a_change() -> None:
    sources = {
        "tests/test_m.py": (
            b"import pytest\n\n\ndef test_a():\n    assert f() == 1\n    assert g() == 2\n\n\n"
            b"def test_b():\n    assert f() == 1\n"
        ),
    }
    head_sources = {
        "tests/test_m.py": (
            b"import pytest\n\n\ndef test_a():\n    assert f() == 1\n\n\n"
            b"@pytest.mark.skip\ndef test_b():\n    assert f() == 1\n"
        ),
    }

    def resolver(files: dict[str, bytes]) -> selection.Resolver:
        return selection.Resolver(
            {"tests/test_m.py": "test_m"},
            lambda paths: {p: files[p] for p in paths if p in files},
        )

    base_map = selection.TestMap(
        {
            "tests/test_m.py::test_a": "test_m:test_a#0",
            "tests/test_m.py::test_b": "test_m:test_b#0",
            "tests/test_m.py::test_gone": "test_m:test_gone#0",
        },
        {},
    )
    head_map = selection.TestMap(
        {
            "tests/test_m.py::test_a": "test_m:test_a#0",
            "tests/test_m.py::test_b": "test_m:test_b#0",
        },
        {},
    )
    changes = [
        SymbolChange("test_m:test_a#0", "modified", "function", "tests/test_m.py", None, None),
        SymbolChange("test_m:test_b#0", "modified", "function", "tests/test_m.py", None, None),
        SymbolChange("pkg.m:f#0", "modified", "function", "pkg/m.py", None, None),
    ]
    found = testdiff.test_changes(
        changes, base_map, head_map, (resolver(sources), resolver(head_sources))
    )
    assert [(c.change, c.test) for c in found] == [
        ("test_removed", "tests/test_m.py::test_gone"),
        ("assertion_removed", "tests/test_m.py::test_a"),
        ("skip_added", "tests/test_m.py::test_b"),
    ]
    for change in found:
        record = change.evidence(RUN)
        models.Evidence.model_validate({**record, "seq": 1})
        models.DiffTestsPayload.model_validate(change.payload(record["id"]))


#: A parametrized test as on Lectris PR #11 (two cases), and three heads of it.
CASES_BASE = b"""import pytest


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1O", "10"), ("O1", "01")],
)
def test_digits(text, expected):
    assert read(text) == expected
"""
#: A case added: the old cases are kept.
CASES_ADDED = CASES_BASE.replace(b'("O1", "01")]', b'("O1", "01"), ("O.5", "0.5")]')
#: A case added and the test's body changed: every case is modified.
CASES_AND_BODY = CASES_ADDED.replace(b"assert read(text)", b"assert read(text.strip())")
#: A case added and the argument names changed: every case is modified.
CASES_AND_NAMES = CASES_ADDED.replace(b'("text", "expected")', b'("given", "expected")')
CASES_FILE = "tests/test_c.py"
CASES_SYMBOL = "test_c:test_digits#0"


def case(name: str) -> str:
    return f"{CASES_FILE}::test_digits[{name}]"


def cases_map(names: Mapping[str, str | None]) -> selection.TestMap:
    """A side's map: each case's ID, and its digest (None: its parameters were not plain)."""
    return selection.TestMap(
        {case(name): CASES_SYMBOL for name in names},
        {},
        cases={case(name): digest for name, digest in names.items() if digest is not None},
    )


def cases_resolvers(head: bytes) -> tuple[selection.Resolver, selection.Resolver]:
    def resolver(source: bytes) -> selection.Resolver:
        return selection.Resolver({CASES_FILE: "test_c"}, lambda paths: {CASES_FILE: source})

    return resolver(CASES_BASE), resolver(head)


CASES_CHANGE = [SymbolChange(CASES_SYMBOL, "modified", "function", CASES_FILE, None, None)]
#: The cases on base, with their digests, and on head with one added.
ON_BASE = {"1O-10": "a" * 32, "O1-01": "b" * 32}
ON_HEAD = {**ON_BASE, "O.5-0.5": "c" * 32}


def test_a_parametrized_test_has_its_cases_in_common_code() -> None:
    base, added, body, names = (
        ast.parse(source).body[1]
        for source in (CASES_BASE, CASES_ADDED, CASES_AND_BODY, CASES_AND_NAMES)
    )
    assert testdiff.parametrized(base)
    assert not testdiff.parametrized(ast.parse(BASE_TEST).body[0])
    assert testdiff.common_code(base) == testdiff.common_code(added)
    assert testdiff.common_code(base) != testdiff.common_code(body)
    assert testdiff.common_code(base) != testdiff.common_code(names)
    ids = b'@pytest.mark.parametrize("x", [1, 2], ids=["one", "two"])\ndef test_x(x):\n    pass\n'
    by_keyword = ids.replace(b'"x", [1, 2]', b'argnames="x", argvalues=[1, 2, 3]').replace(
        b'"two"]', b'"two", "three"]'
    )
    other_mark = ids.replace(b"def test_x", b"@pytest.mark.slow\ndef test_x")
    first, second, third = (ast.parse(s).body[0] for s in (ids, by_keyword, other_mark))
    assert testdiff.common_code(first) != testdiff.common_code(second)  # argnames by keyword
    assert testdiff.common_code(first) != testdiff.common_code(third)
    assert "argvalues" not in testdiff.common_code(second)


def test_a_case_added_to_a_parametrized_test_leaves_the_others_kept() -> None:
    """Lectris PR #11: cases added to a parametrized test; the old ones are the same tests."""
    base, head = cases_map(ON_BASE), cases_map(ON_HEAD)
    kept = testdiff.kept_cases(CASES_CHANGE, base, head, cases_resolvers(CASES_ADDED))
    assert kept == {case("1O-10"), case("O1-01")}
    found = testdiff.test_changes(CASES_CHANGE, base, head, cases_resolvers(CASES_ADDED))
    assert [(c.change, c.test, c.detail) for c in found] == [
        (
            "test_modified",
            f"{CASES_FILE}::test_digits",
            "only its parametrize cases changed: 1 added, 0 removed, 2 kept their parameters",
        )
    ]
    assert found[0].status == "warn"


@pytest.mark.parametrize(
    ("head", "on_head", "kept", "detail"),
    [
        # a case's own parameters changed under the same ID: that case is modified
        (
            CASES_ADDED,
            {**ON_HEAD, "O1-01": "d" * 32},
            ["1O-10"],
            "1 added, 0 removed, 1 kept their parameters, 1 changed theirs",
        ),
        # parameters that are not plain values cannot be compared: modified
        (
            CASES_ADDED,
            {**ON_HEAD, "1O-10": None},
            ["O1-01"],
            "1 added, 0 removed, 1 kept their parameters, 1 could not be compared",
        ),
        # a case removed
        (
            CASES_ADDED,
            {"1O-10": "a" * 32},
            ["1O-10"],
            "0 added, 1 removed, 1 kept their parameters",
        ),
        # the test's own code changed too: every case is modified
        (CASES_AND_BODY, ON_HEAD, [], None),
        (CASES_AND_NAMES, ON_HEAD, [], None),
    ],
)
def test_only_cases_with_the_same_parameters_are_kept(
    head: bytes, on_head: dict[str, str | None], kept: list[str], detail: str | None
) -> None:
    base, head_map = cases_map(ON_BASE), cases_map(on_head)
    found = testdiff.kept_cases(CASES_CHANGE, base, head_map, cases_resolvers(head))
    assert found == {case(name) for name in kept}
    changes = testdiff.test_changes(CASES_CHANGE, base, head_map, cases_resolvers(head))
    details = [c.detail for c in changes if c.change == "test_modified"]
    expected = (
        f"only its parametrize cases changed: {detail}" if detail else "the test's code changed"
    )
    assert details == [expected]


def test_a_test_that_is_not_parametrized_is_never_kept() -> None:
    source = b"def test_digits():\n    assert read('1O') == '10'\n"
    edited = source.replace(b"'10'", b"'1O'")

    def resolver(text: bytes) -> selection.Resolver:
        return selection.Resolver({CASES_FILE: "test_c"}, lambda paths: {CASES_FILE: text})

    tests = selection.TestMap({f"{CASES_FILE}::test_digits": CASES_SYMBOL}, {})
    resolvers = (resolver(source), resolver(source))  # the same code: only a comment changed
    assert testdiff.kept_cases(CASES_CHANGE, tests, tests, resolvers) == frozenset()
    found = testdiff.test_changes(CASES_CHANGE, tests, tests, (resolver(source), resolver(edited)))
    assert [c.detail for c in found] == ["the test's code changed"]
