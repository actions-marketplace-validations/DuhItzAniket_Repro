"""What a reviewer sees first: the evidence listing's order and its collapsed lines (G03).

A listing is cut at ``MAX_ANSWER_CHARS``, and a reviewer judges by what it saw. The situation
these tests rebuild is a real one: a correct change that adds cases to two parametrized tests
makes every old case of them ``modified`` (the test's code changed) with the same outcome on
both sides. Ordered by type and target, 31 such lines filled a reviewer's first listing, and
neither the new cases that show the fix nor the probe that does reached it; two reviewers
answered "unverifiable". The evidence here is synthetic, in that run's shape.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from gitrepo import Repo
from repro import __version__
from repro.agents.evidence import (
    BEHAVIOUR,
    CODE_ONLY,
    FAILING,
    OTHER,
    OUTCOME,
    UNCHANGED,
    EvidenceView,
    Item,
    group_key,
    rank,
    unchanged,
    unparametrized,
)
from repro.agents.prompts import load_prompt
from repro.agents.reviewer import relevant, reviewer_request
from repro.agents.sources import RepoSources
from repro.agents.tools import COLLAPSE_AT, MAX_ANSWER_CHARS, EvidenceTools
from repro.gitops import Git
from repro.ids import new_evidence_id

CODE = "pkg/numbers.py"
IN_NUMBERS = "pkg.numbers:in_numbers#0"
TESTS = "tests/test_numbers.py::TestInNumbers"
LEFT_ALONE = f"{TESTS}::test_codes_are_left_alone"  # sorts before the other, as on the real run
DIGITS = f"{TESTS}::test_letters_become_digits"


def record(
    kind: str,
    targets: list[dict[str, str]],
    summary: str,
    origin: str = "introduced",
    stability: str = "stable",
    **data: Any,
) -> dict[str, Any]:
    return {
        "id": new_evidence_id(),
        "run_id": "run_" + "0" * 26,
        "type": kind,
        "targets": targets,
        "summary": summary,
        "data": data,
        "artifacts": [],
        "stability": stability,
        "origin": origin,
        "produced_by": {"component": "tests", "version": __version__},
    }


def outcome(test: str, kind: str, base: str | None, head: str | None) -> dict[str, Any]:
    said = f"{kind}: {test} (base {base or 'absent'}, head {head or 'absent'})"
    again = base if kind != "new" else None
    origin = {"P2P": "n/a", "F2F": "pre-existing"}.get(kind, "introduced")
    found = record("test_outcome", [{"test": test}], said, origin=origin)
    found["data"] = {
        "class": kind,
        "base_outcome": base,
        "base_again_outcome": again,
        "head_outcome": head,
    }
    return found


def item(found: dict[str, Any]) -> Item:
    return EvidenceView.from_events(events([found])).items[0]


def events(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"kind": "evidence.created", "payload": {"evidence": r}} for r in found]


def change_shaped() -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """The real run's evidence, in kind and number: 22 + 9 old cases ``modified`` and passing
    on both sides, 6 new negative and 4 new positive cases, the two tests' ``test_modified``,
    one probe difference, 187 tests that passed on both sides and 74 unchanged timings. Also
    which records are which, by ID."""
    old_left = [
        outcome(f"{LEFT_ALONE}[1{n:02d}B5]", "modified", "passed", "passed") for n in range(22)
    ]
    old_digits = [
        outcome(f"{DIGITS}[{n}O5-{n}05]", "modified", "passed", "passed") for n in range(9)
    ]
    negatives = [outcome(f"{LEFT_ALONE}[O{n} road]", "new", None, "passed") for n in range(6)]
    positives = [outcome(f"{DIGITS}[O.{n}-0.{n}]", "new", None, "passed") for n in range(4)]
    edited = [
        record(
            "test_change",
            [{"file": "tests/test_numbers.py", "test": test}],
            f"test_modified: {test}: the test's code changed",
            stability="n/a",
            change="test_modified",
            detail="the test's code changed",
        )
        for test in (LEFT_ALONE, DIGITS)
    ]
    probe = record(
        "probe_diff",
        [{"file": CODE, "symbol": IN_NUMBERS}],
        "in_numbers(text='O.5') gives another result: head '0.5', base 'O.5'",
        change="result_changed",
        provenance="generated",
        reproducer={"call": "in_numbers(text='O.5')", "base": "'O.5'", "head": "'0.5'"},
    )
    passed = [
        outcome(f"tests/test_cli.py::test_{n:03d}", "P2P", "passed", "passed") for n in range(187)
    ]
    timed = [
        record(
            "perf_diff",
            [{"test": f"tests/test_cli.py::test_{n:03d}"}],
            "no_change",
            verdict="no_change",
        )
        for n in range(74)
    ]
    found = [*old_left, *old_digits, *negatives, *positives, *edited, probe, *passed, *timed]
    ids = {
        "old": [r["id"] for r in (*old_left, *old_digits)],
        "negatives": [r["id"] for r in negatives],
        "positives": [r["id"] for r in positives],
        "edited": [r["id"] for r in edited],
        "probe": [probe["id"]],
    }
    return found, ids


def change_view() -> tuple[EvidenceView, dict[str, list[str]]]:
    found, ids = change_shaped()
    selected = [
        {
            "kind": "test.selected",
            "target": {"test": r["targets"][0]["test"]},
            "payload": {"symbols": [IN_NUMBERS]},
        }
        for r in found
        if r["type"] == "test_outcome" and r["targets"][0]["test"].startswith(TESTS)
    ]
    changed = {"kind": "symbol.changed", "payload": {"symbol_id": IN_NUMBERS, "file": CODE}}
    return EvidenceView.from_events([changed, *selected, *events(found)]), ids


@pytest.fixture
def sources(tmp_path: Path) -> RepoSources:
    repo = Repo(tmp_path / "repo")
    base = repo.commit({CODE: "def in_numbers(text):\n    return text\n"})
    head = repo.commit({CODE: "def in_numbers(text):\n    return text.replace('O.', '0.')\n"})
    return RepoSources(Git(repo.path), base, head)


def listed(tools: EvidenceTools, target: dict[str, str]) -> list[str]:
    answer = asyncio.run(tools.list_evidence(target))
    return [line for line in answer.split("\n") if line.startswith("ev_")]


def ids_in(lines: list[str]) -> list[str]:
    return [line.split()[0] for line in lines]


@pytest.mark.parametrize(
    ("found", "expected"),
    [
        (outcome("t.py::a", "P2F", "passed", "failed"), FAILING),
        (outcome("t.py::a", "new", None, "failed"), FAILING),
        (outcome("t.py::a", "modified", "passed", "failed"), FAILING),
        (outcome("t.py::a", "modified", "failed", "failed"), FAILING),
        (record("test_change", [{"test": "t.py::a"}], "s", change="assertion_removed"), FAILING),
        (record("invariant", [{"file": "a.py"}], "s", invariant="i", result="fail"), FAILING),
        (record("perf_diff", [{"test": "t.py::a"}], "s", verdict="regression"), FAILING),
        (record("policy_change", [{"file": "repro.yml"}], "s", change="modified"), FAILING),
        (record("behaviour_diff", [{"symbol": "m:f#0"}], "s", change="result_changed"), BEHAVIOUR),
        (record("probe_diff", [{"symbol": "m:f#0"}], "s", change="result_changed"), BEHAVIOUR),
        (record("api_diff", [{"symbol": "m:f#0"}], "s", breakage="removed"), BEHAVIOUR),
        (outcome("t.py::a", "F2P", "failed", "passed"), OUTCOME),
        (outcome("t.py::a", "new", None, "passed"), OUTCOME),
        (outcome("t.py::a", "removed", "passed", None), OUTCOME),
        (outcome("t.py::a", "modified", "failed", "passed"), OUTCOME),
        (outcome("t.py::a", "F2F", "failed", "failed"), OTHER),
        (record("test_change", [{"test": "t.py::a"}], "s", change="test_modified"), OTHER),
        (record("dep_diff", [{"file": "uv.lock"}], "s", package="p", change="upgraded"), OTHER),
        (record("invariant", [{"file": "a.py"}], "s", invariant="i", result="pass"), OTHER),
        (record("perf_diff", [{"test": "t.py::a"}], "s", verdict="improvement"), OTHER),
        (outcome("t.py::a", "modified", "passed", "passed"), CODE_ONLY),
        (outcome("t.py::a", "P2P", "passed", "passed"), UNCHANGED),
        (record("perf_diff", [{"test": "t.py::a"}], "s", verdict="no_change"), UNCHANGED),
    ],
)
def test_each_item_has_its_place_in_a_listing(found: dict[str, Any], expected: int) -> None:
    assert rank(item(found)) == expected


def test_a_modified_test_that_flipped_between_its_base_runs_is_not_code_only() -> None:
    flipped = outcome("t.py::a", "modified", "passed", "passed")
    flipped["data"]["base_again_outcome"] = "failed"
    assert rank(item(flipped)) == OUTCOME


@pytest.mark.parametrize(
    ("test", "expected"),
    [
        ("tests/t.py::test_a[x]", "tests/t.py::test_a"),
        ("tests/t.py::C::test_a[O,75 litre-0,75 litre]", "tests/t.py::C::test_a"),
        ("tests/t.py::test_a[a::b]", "tests/t.py::test_a"),
        ("tests/t.py::test_a", "tests/t.py::test_a"),
        ("tests/x[1]/t.py::test_a", "tests/x[1]/t.py::test_a"),
        ("src/a.test.ts::adds [x]", "src/a.test.ts::adds [x]"),  # a JavaScript test's name
        ("test_a[x]", "test_a[x]"),
    ],
)
def test_a_parametrized_case_belongs_to_its_test(test: str, expected: str) -> None:
    assert unparametrized(test) == expected


def test_the_cases_of_a_parametrized_test_are_alike() -> None:
    first, second = (outcome(f"t.py::test_a[{n}]", "modified", "passed", "passed") for n in "xy")
    other = outcome("t.py::test_b[x]", "modified", "passed", "passed")
    assert (
        group_key(item(first))
        == group_key(item(second))
        == ("test_outcome", "modified", "t.py::test_a")
    )
    assert group_key(item(other)) != group_key(item(first))


def test_the_situation_is_rebuilt_faithfully() -> None:
    view, ids = change_view()
    differing = [i.id for i in view.items if not unchanged(i)]
    assert len(differing) == 44  # 31 old cases, 10 new ones, 2 edited tests, 1 probe
    assert set(differing[:22]) <= set(ids["old"])  # by type and target, the old cases first


def test_the_first_listing_shows_the_new_cases_and_the_probe(sources: RepoSources) -> None:
    view, ids = change_view()
    tools = EvidenceTools(view, sources)
    answer = asyncio.run(tools.list_evidence({}))
    assert answer.startswith("305 evidence items (the most decisive first;")
    lines = listed(tools, {})
    shown = ids_in(lines)
    assert shown[0] == ids["probe"][0]
    for kind in ("positives", "negatives", "edited"):
        assert set(ids[kind]) <= set(shown), kind
    old = [line for line in lines if line.split()[0] in ids["old"]]
    assert len(old) == 2  # each test's old cases on one line
    assert (
        "[and 21 more like it (the same outcome on base and head, the test's code changed): "
        in old[0]
    )
    assert f"list_evidence with test {LEFT_ALONE} lists each]" in old[0]
    assert "[and 8 more like it" in old[1]
    assert f"with test {DIGITS} lists each]" in old[1]
    assert len("\n".join(lines)) <= MAX_ANSWER_CHARS
    differing = 1 + 10 + 2 + 2  # lines standing for the 44 items that differ
    assert all(" P2P: " in line for line in lines[differing:])
    assert f"...and {305 - 44 - (len(lines) - differing)} more;" in answer


def test_the_claim_file_listing_shows_them_too(sources: RepoSources) -> None:
    view, ids = change_view()
    shown = ids_in(listed(EvidenceTools(view, sources), {"file": CODE}))
    assert shown[0] == ids["probe"][0]  # about the file itself
    assert set(ids["positives"]) <= set(shown)


def test_listing_a_test_shows_its_own_cases_each(sources: RepoSources) -> None:
    view, ids = change_view()
    lines = listed(EvidenceTools(view, sources), {"test": DIGITS})
    shown = ids_in(lines)
    own = [*ids["positives"], ids["edited"][1], *ids["old"][22:]]
    assert set(shown[: len(own)]) == set(own)  # its own first, each of its 9 old cases too
    assert not any("more like it" in line for line in lines[: len(own)])
    assert any("[and 21 more like it" in line for line in lines)  # the other test, collapsed


def test_a_case_of_a_test_lists_the_test_s_cases_first(sources: RepoSources) -> None:
    view, ids = change_view()
    shown = ids_in(listed(EvidenceTools(view, sources), {"test": f"{DIGITS}[O.5]"}))
    assert set(shown[:4]) == set(ids["positives"])


def test_fewer_alike_items_than_collapse_at_are_listed_each(sources: RepoSources) -> None:
    few = [
        outcome(f"t.py::test_a[{n}]", "modified", "passed", "passed")
        for n in range(COLLAPSE_AT - 1)
    ]
    lines = listed(EvidenceTools(EvidenceView.from_events(events(few)), sources), {})
    assert len(lines) == COLLAPSE_AT - 1
    assert not any("more like it" in line for line in lines)


def test_the_same_evidence_lists_the_same_whatever_its_order(sources: RepoSources) -> None:
    found, _ = change_shaped()
    once = EvidenceTools(EvidenceView.from_events(events(found)), sources)
    again = EvidenceTools(EvidenceView.from_events(events(found[::-1])), sources)
    for target in ({}, {"file": CODE}, {"test": DIGITS}):
        assert asyncio.run(once.list_evidence(target)) == asyncio.run(again.list_evidence(target))


def test_the_reviewer_is_given_the_probe_and_the_new_cases_first(sources: RepoSources) -> None:
    view, ids = change_view()
    claim = {
        "id": "C1",
        "text": "Read an O before a decimal point as a leading zero",
        "kind": "fix",
        "targets": [{"file": CODE}],
        "source": "title",
    }
    items = relevant(view, claim)
    request = reviewer_request(
        claim, items, sources, tools=(), prompt=load_prompt("reviewer"), model="m"
    )
    shown = [line.split()[1] for line in request.prompt.split("\n") if line.startswith("evidence ")]
    assert shown[0] == ids["probe"][0]
    assert {ids["negatives"][0], ids["positives"][0]} <= set(shown[1:3])
    assert set(shown[3:]) <= set(ids["old"]) | set(ids["edited"])
    for positive in ids["positives"][1:]:
        assert positive in request.prompt  # the group's other IDs, citable
