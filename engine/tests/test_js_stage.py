"""A JavaScript change through ``repro run``'s pipeline to its card (K08, K11; ADR-0017, phase 4).

Real Vitest runs: the monorepo's own Vitest stands in for the project's (``JsOptions``), so no
dependency is installed. The pinned TypeScript compiler (K11) is fetched once per checkout into
pytest's cache (``ts_support``); without it, the runs record what it would have checked as
missing. Each run's log must be schema-valid and its card assembled again from it byte for byte,
as a Python run's (``test_run_stage``).
"""

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import ts_support
from gitrepo import Entry, Repo
from graph_support import check_selection_graph
from repro import language
from repro.analysis import javascript
from repro.bus import EventBus
from repro.card import regenerate
from repro.card.job import StoreCards
from repro.card.markdown import render
from repro.gitops import Git
from repro.ids import new_run_id
from repro.js_stage import JsOptions, base_copy
from repro.orchestrator import Orchestrator
from repro.pipeline import Change, pipeline_jobs, run_backend
from repro.store import ArtifactStore, StoreReader, StoreWriter
from repro_events.log import read_log, validate_log
from repro_events.schemas import load_schema_set

ROOT = Path(__file__).resolve().parents[2]
VITEST = ROOT / "apps" / "web" / "node_modules" / "vitest" / "vitest.mjs"
NEEDS_VITEST = pytest.mark.skipif(
    shutil.which("node") is None or not VITEST.is_file(),
    reason="needs node and the monorepo's Vitest (pnpm install)",
)

PROJECT = {
    "package.json": json.dumps({"type": "module", "devDependencies": {"vitest": "5.0.4"}}),
    "package-lock.json": "{}",
    "vitest.config.mjs": "export default { test: { globals: true } };\n",
}
SOURCE = (
    "export function add(a, b) {\n  return a + b;\n}\n\n"
    'export function label(n) {\n  return "n=" + n.toFixed(1);\n}\n'
)
TESTS = (
    'import { add, label } from "../src/m.js";\n'
    'describe("m", () => {\n'
    '  test("adds", () => {\n'
    "    expect(add(1, 2)).toBe(3);\n"
    "    expect(add(0, 0)).toBe(0);\n"
    "  });\n"
    '  test("labels", () => {\n    expect(label(2)).toContain("n=");\n  });\n'
    '  test("old", () => {\n    expect(add(2, 2)).toBe(4);\n  });\n'
    "});\n"
)


@pytest.fixture
def tools(pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Repro's tools for the runs: the pinned TypeScript compiler is fetched there once (K11)."""
    return ts_support.tools(pytestconfig, tmp_path_factory)


def reproduce(
    tmp_path: Path,
    head: dict[str, Entry | None],
    tools: Path,
    base: dict[str, Entry] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Base (``PROJECT``, ``SOURCE``, ``TESTS``, and ``base``) against base with ``head``
    applied: the run's events, checked, and its card."""
    repo = Repo(tmp_path / "repo")
    base_sha = repo.commit({**PROJECT, "src/m.js": SOURCE, "test/m.test.js": TESTS, **(base or {})})
    head_sha = repo.commit(head)
    git = Git(repo.path)
    found = language.detect(git, base_sha, head_sha)
    change = replace(Change.resolve(git, repo.path, base_sha, head_sha), language=found)
    database = tmp_path / "repro.db"
    writer = StoreWriter(database).start()
    run_id = new_run_id()
    try:
        jobs, state = pipeline_jobs(
            git,
            change,
            tmp_path / "work",
            cache=tmp_path / "cache",
            analyse_only=False,
            artifacts=ArtifactStore(tmp_path / "runs", writer),
            cards=StoreCards(database, writer),
            js=JsOptions(install=False, entry=str(VITEST), tools=tools),
        )
        result = Orchestrator(EventBus(writer), concurrency=2).run(
            change.spec(), jobs, run_id=run_id
        )
        state.close()
        assert result.outcome == "completed", result.jobs
    finally:
        writer.close()
    with StoreReader(database) as reader:
        events = [json.loads(e.body) for e in reader.iter_events(run_id)]
    log = tmp_path / "run.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    lines, issues = read_log(log)
    assert issues + validate_log(load_schema_set(), lines) == []
    (published,) = [e for e in events if e["kind"] == "card.published"]
    card = published["payload"]["card"]
    assert regenerate(events).identical
    return events, card


def payloads(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in events if e["kind"] == kind]


def evidence(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [
        p["evidence"] for p in payloads(events, "evidence.created") if p["evidence"]["type"] == kind
    ]


def reasons(card: dict[str, Any]) -> set[str]:
    return {item["reason"] for item in card["missing"]}


@NEEDS_VITEST
def test_a_regression_and_a_silent_change_flag_the_card(tmp_path: Path, tools: Path) -> None:
    head = SOURCE.replace("return a + b;", "return a - b;").replace("n.toFixed(1)", "n")
    events, card = reproduce(tmp_path, {"src/m.js": head}, tools)
    assert payloads(events, "run.started")[0]["language"] == "javascript"
    changed = {(p["symbol_id"], p["change"]) for p in payloads(events, "symbol.changed")}
    assert changed == {("src/m.js:add#0", "modified"), ("src/m.js:label#0", "modified")}
    selected = {
        e["target"]["test"]: e["payload"]["reasons"] for e in events if e["kind"] == "test.selected"
    }
    assert selected == {
        "test/m.test.js::m adds": ["blast_radius"],
        "test/m.test.js::m labels": ["blast_radius"],
        "test/m.test.js::m old": ["blast_radius"],
    }
    check_selection_graph(events)  # B14: the selected tests as nodes, strands' sources
    covers = {
        (e["payload"]["source"], e["payload"]["target"])
        for e in events
        if e["kind"] == "graph.edge" and e["payload"]["type"] == "covers"
    }
    assert ("test/m.test.js::m adds", "src/m.js:add#0") in covers
    assert ("test/m.test.js::m labels", "src/m.js:label#0") in covers
    classes = {
        o["targets"][0]["test"]: o["data"]["class"] for o in evidence(events, "test_outcome")
    }
    # add(0, 0) is 0 either way, but add(1, 2) is not: three reruns a side confirm the flip.
    assert classes == {
        "test/m.test.js::m adds": "P2F",
        "test/m.test.js::m labels": "P2P",
        "test/m.test.js::m old": "P2F",
    }
    found = evidence(events, "behaviour_diff")
    behaviour = {(b["targets"][0]["symbol"], b["data"]["change"]) for b in found}
    assert ("src/m.js:label#0", "result_changed") in behaviour  # every test passes: silent
    assert card["status"] == "FLAGGED"
    conditions = card["conditions"]
    assert conditions["language"] == "javascript"
    assert conditions["python_version"] is None
    assert conditions["node_version"]
    assert (conditions["isolation_level"], conditions["shuffle_seed"]) in {("L2", 1), ("L1", 1)}
    assert conditions["seeds"] is None
    assert {"unsupported_project", "perf_not_assessable", "network_not_enforced"} <= reasons(card)
    assert "Node.js" in render(card)


@NEEDS_VITEST
def test_edited_tests_are_checked_for_what_an_agent_does_to_them(
    tmp_path: Path, tools: Path
) -> None:
    tests = (
        TESTS.replace("    expect(add(0, 0)).toBe(0);\n", "")  # one assertion fewer
        .replace('test("labels"', 'test.skip("labels"')  # skipped
        .replace('  test("old", () => {\n    expect(add(2, 2)).toBe(4);\n  });\n', "")  # gone
        .removesuffix("});\n")
    ) + '  test("new", () => {\n    expect(add(1, 1)).toBe(2);\n  });\n});\n'
    events, card = reproduce(tmp_path, {"test/m.test.js": tests}, tools)
    selected = {
        e["target"]["test"]: e["payload"]["reasons"] for e in events if e["kind"] == "test.selected"
    }
    assert selected["test/m.test.js::m adds"] == ["modified_test"]
    assert selected["test/m.test.js::m new"] == ["new_test"]
    check_selection_graph(events)
    found = evidence(events, "test_change")
    changes = {(t["targets"][0].get("test"), t["data"]["change"]) for t in found}
    assert changes == {
        ("test/m.test.js::m adds", "assertion_removed"),
        ("test/m.test.js::m labels", "skip_added"),
        ("test/m.test.js::m old", "test_removed"),
    }
    assert card["status"] == "FLAGGED"
    assert "unsupported_project" in reasons(card)  # what Python checks and this does not


@NEEDS_VITEST
def test_a_change_that_keeps_behaviour_is_verified(tmp_path: Path, tools: Path) -> None:
    head = SOURCE.replace("  return a + b;", "  const sum = a + b;\n  return sum;")
    events, card = reproduce(tmp_path, {"src/m.js": head}, tools)
    assert payloads(events, "diff.behaviour") == []
    assert {o["data"]["class"] for o in evidence(events, "test_outcome")} == {"P2P"}
    assert card["status"] == "VERIFIED", card["status_reasons"]
    # Verified, and what was not checked is on the card.
    assert {"unsupported_project", "perf_not_assessable"} <= reasons(card)


def test_a_javascript_run_takes_a_container_under_auto_as_python_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K11: with a container that has Node, ``auto`` means Docker when it answers, as for
    Python; ``run.started`` names what the jobs use."""
    monkeypatch.setattr("repro.run_stage.docker_available", lambda: True)
    assert run_backend("auto", None, runs_code=True) == "docker"
    assert run_backend("auto", "runsc", runs_code=True) == "gvisor"
    assert run_backend("process", None, runs_code=True) == "process"
    monkeypatch.setattr("repro.run_stage.docker_available", lambda: False)
    assert run_backend("auto", None, runs_code=True) == "process"


@NEEDS_VITEST
def test_the_public_api_and_a_weakened_assertion_by_typescript(
    tmp_path: Path, pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # K11: the package's main is src/m.js. Head adds a parameter to label, which every call
    # leaves out, and the test of label checks less.
    tools = ts_support.pinned(pytestconfig, tmp_path_factory).parent
    manifest = json.dumps({**json.loads(PROJECT["package.json"]), "main": "src/m.js"})
    head: dict[str, Entry | None] = {
        "src/m.js": SOURCE.replace("label(n)", "label(n, digits)").replace(
            "n.toFixed(1)", "n.toFixed(digits ?? 1)"
        ),
        "test/m.test.js": TESTS.replace(
            'expect(label(2)).toContain("n=")', "expect(label(2)).toBeTruthy()"
        ),
    }
    events, card = reproduce(tmp_path, head, tools, base={"package.json": manifest})
    (api,) = payloads(events, "diff.api")
    assert (api["symbol_id"], api["breakage"]) == ("src/m.js:label#0", "parameter_added_required")
    assert api["explanation"] == (
        "label in the package: Parameter was added as required (digits: any)"
    )
    (break_,) = evidence(events, "api_diff")
    assert break_["data"]["source"] == "typescript"
    found = evidence(events, "test_change")
    assert [(t["targets"][0]["test"], t["data"]["change"], t["data"]["detail"]) for t in found] == [
        (
            "test/m.test.js::m labels",
            "assertion_weakened",
            'a check became a looser one: toContain("…") → toBeTruthy()',
        )
    ]
    assert payloads(events, "diff.behaviour") == []  # label(2) returns what it did
    assert card["status"] == "FLAGGED"
    # Every check of an edited test ran: none is missing.
    assert not [m for m in card["missing"] if "edited tests" in m["detail"]]


def test_the_target_language_is_read_from_the_root(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "repo")
    js = repo.commit(PROJECT)
    both = repo.commit({"pyproject.toml": "[project]\nname = 'x'\n"})
    git = Git(repo.path)
    assert language.detect(git, js, js) == "javascript"
    assert language.detect(git, js, both) == "python"  # both: Python's, the complete pipeline
    none = repo.commit({"package.json": None, "pyproject.toml": None}, fresh=True)
    assert language.detect(git, none, none) == "python"


def test_imports_resolve_as_node_and_bundlers_resolve_them() -> None:
    files = {"src/a.ts", "src/b.js", "src/lib/index.ts", "src/c.mts", "test/a.test.ts"}
    found = {
        spec: javascript.resolve("src/a.ts", spec, files)
        for spec in ("./b.js", "./b", "./lib", "./c.mjs", "../test/a.test", "lodash", "../../x")
    }
    assert found == {
        "./b.js": "src/b.js",
        "./b": "src/b.js",
        "./lib": "src/lib/index.ts",
        "./c.mjs": "src/c.mts",  # TypeScript's .mjs name for its .mts source
        "../test/a.test": "test/a.test.ts",
        "lodash": None,
        "../../x": None,
    }
    source = (
        'import x from "./b.js";\n// import y from "./nope";\nexport * from "./lib";\n'
        'const s = "require(\'./nope\')";\nconst c = require("./c.mjs");\n'
        'import "./side";\nconst d = await import("./d");\n'
    )
    assert javascript.specifiers(source) == ["./b.js", "./lib", "./c.mjs", "./side", "./d"]
    sources = ["src/a.ts", "src/a.d.ts", "src/b.d.mts", "node_modules/x/i.js", "README.md"]
    assert [path for path in sources if javascript.is_source(path)] == ["src/a.ts"]


@NEEDS_VITEST
def test_a_test_edited_to_fit_a_regression_is_caught_by_its_base_version(
    tmp_path: Path, pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """K11: base's version of an edited test runs against head's code. Head breaks ``add`` and
    edits the test to expect the broken result: head's test passes, base's fails on head."""
    head = SOURCE.replace("return a + b;", "return a - b;")
    tests = TESTS.replace("expect(add(1, 2)).toBe(3);", "expect(add(1, 2)).toBe(-1);").replace(
        "expect(add(2, 2)).toBe(4);", "expect(add(2, 2)).toBe(0);"
    )
    tools = ts_support.pinned(pytestconfig, tmp_path_factory).parent
    events, card = reproduce(tmp_path, {"src/m.js": head, "test/m.test.js": tests}, tools)
    found = evidence(events, "test_change")
    changes = {(t["targets"][0].get("test"), t["data"]["change"]) for t in found}
    assert ("test/m.test.js::m adds", "base_test_fails_on_head") in changes
    assert ("test/m.test.js::m old", "base_test_fails_on_head") in changes
    assert card["status"] == "FLAGGED"
    assert not (tmp_path / "work").exists() or not list((tmp_path / "work").rglob("*.repro-base.*"))


def test_base_versions_go_beside_the_test_file() -> None:
    assert base_copy("test/m.test.ts") == "test/m.repro-base.test.ts"
    assert base_copy("src/a.spec.mjs") == "src/a.repro-base.spec.mjs"
    assert base_copy("__tests__/b.js") == "__tests__/b.repro-base.js"
