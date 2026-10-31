"""``repro run`` end to end with the run stage (D06): sandboxes, environments, both sides' tests,
and the evidence as stored events.

The fixtures' expectation files name the evidence and missing items; here they must arrive as
``evidence.created`` / ``missing.recorded`` events of a schema-valid log, with ``test.selected``,
``diff.outcome`` and ``diff.tests`` events citing them. The process sandbox runs everywhere; the
container case needs Docker with Linux containers (required on CI's Ubuntu job).
"""

import hashlib
import json
import os
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from conftest import Golden
from typer.testing import CliRunner

from card_support import normalised
from cli_support import clean_env
from fixtures.repo_builder import build_repo
from fixtures.specs import ALL_SPECS
from graph_support import check_selection_graph
from repro.card import canonical, digest, regenerate
from repro.cli import app
from repro.config import load_settings
from repro.policy.gap import inputs_from_events
from repro.policy.status import decide
from repro.sandbox import docker_available
from repro.store import StoreReader
from repro_events.log import read_log, validate_log
from repro_events.schemas import load_schema_set

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def run_fixture(name: str, tmp_path: Path, sandbox: str, **variables: str) -> list[dict[str, Any]]:
    expected = tomllib.loads((FIXTURES / f"{name}.toml").read_text(encoding="utf-8"))
    built = build_repo(ALL_SPECS[name], tmp_path / "repo")
    head = built.head_shas[expected["fixture"]["head"]]
    scratch = str(tmp_path / "scratch")
    env = clean_env(tmp_path, REPRO_SANDBOX=sandbox, REPRO_RUN_ROOT=scratch, **variables)
    argv = ["run", "--repo", str(built.path), "--base", built.base_sha, "--head", head]
    result = runner.invoke(app, [*argv, "--no-serve"], env=env)
    assert result.exit_code == 0, result.output
    with StoreReader(load_settings(env=env).paths.database) as reader:
        (run,) = reader.runs(10)
        events = [json.loads(event.body) for event in reader.events(run.id, 0, 100_000)]
        stored_card = reader.card(run.id)
    log = tmp_path / "run.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    lines, issues = read_log(log)
    assert issues + validate_log(load_schema_set(), lines) == []
    assert not list((tmp_path / "scratch").iterdir())  # the run left nothing behind
    check_card(events, stored_card)
    return events


def check_card(events: list[dict[str, Any]], stored: dict[str, Any] | None) -> None:
    """F05: one card, published with the run's status, recorded in the store, and assembled
    again from the log byte for byte."""
    (published,) = [e for e in events if e["kind"] == "card.published"]
    card = published["payload"]["card"]
    assert published["payload"]["card_sha256"] == digest(canonical(card))
    assert stored == card
    found = regenerate(events)
    assert found.identical, (found.sha256, found.published_sha256)
    assert found.card == card
    finished = events[-1]
    assert (finished["kind"], finished["payload"]["card_status"]) == (
        "run.finished",
        card["status"],
    )


def started_backend(events: list[dict[str, Any]]) -> str:
    """The sandbox ``run.started`` names: the one the run's sandbox jobs used."""
    (started,) = [e["payload"] for e in events if e["kind"] == "run.started"]
    return str(started["mode"]["sandbox_backend"])


def evidence_name(item: dict[str, Any]) -> str | None:
    """An evidence item as the expectation files name it: ``<type>:<detail>:<target>``
    (a dependency's: ``dep_diff:<change>/<source>:<package>``)."""
    data, target = item["data"], item["targets"][0]
    if item["type"] == "dep_diff":
        return f"dep_diff:{data['change']}/{data['source']}:{data['package']}"
    if item["type"] == "policy_change":
        return f"policy_change:{data['change']}:{target['file']}"
    detail = {
        "test_outcome": "class",
        "test_change": "change",
        "perf_diff": "verdict",
        "behaviour_diff": "change",
        "api_diff": "breakage",
        "probe_diff": "change",
    }.get(item["type"])
    if detail is None:
        return None
    where = target.get("test") or target.get("symbol") or target["file"]
    return f"{item['type']}:{data[detail]}:{where}"


def check_against_expectation(name: str, events: list[dict[str, Any]]) -> None:
    expected = tomllib.loads((FIXTURES / f"{name}.toml").read_text(encoding="utf-8"))
    log = "\n".join(
        e["payload"]["message"] for e in events if e["kind"] == "log.line"
    )  # shown when an assertion fails
    selected = {
        e["target"]["test"]: e["payload"]["reasons"] for e in events if e["kind"] == "test.selected"
    }
    assert selected == expected["selection"].get("selected", {}), log
    check_selection_graph(events)  # B14: the selected tests as nodes, strands' sources
    evidence = {
        e["payload"]["evidence"]["id"]: e["payload"]["evidence"]
        for e in events
        if e["kind"] == "evidence.created"
    }
    found = [named for item in evidence.values() if (named := evidence_name(item)) is not None]
    assert sorted(found) == sorted(expected["expected"]["evidence"]), log
    diffs = (
        "diff.outcome",
        "diff.tests",
        "diff.perf",
        "diff.behaviour",
        "diff.api",
        "diff.deps",
        "diff.probe",
    )
    cited = [e["payload"]["evidence_id"] for e in events if e["kind"] in diffs]
    compared = (
        "test_outcome",
        "test_change",
        "perf_diff",
        "behaviour_diff",
        "api_diff",
        "dep_diff",
        "probe_diff",
    )
    assert sorted(cited) == sorted(i for i, item in evidence.items() if item["type"] in compared)
    for event in events:  # ADR-0015 §2: what each check's status means without claims
        payload = event["payload"]
        if event["kind"] == "diff.outcome":
            kind = payload["class"]
            passing = payload["head_outcome"] in ("passed", "xfailed", "xpassed")
            expected_status = (
                "ok"
                if kind in ("P2P", "F2P") or (kind == "new" and passing)
                else "fail"
                if kind in {"P2F", "new"}
                else "warn"
            )
        elif event["kind"] == "diff.tests":
            expected_status = "warn" if payload["change"] == "test_modified" else "fail"
        elif event["kind"] == "diff.perf":
            expected_status = "ok" if payload["verdict"] in ("improvement", "no_change") else "warn"
        elif event["kind"] in ("diff.behaviour", "diff.api", "diff.deps", "diff.probe"):
            expected_status = "warn"  # every difference needs a claim to explain it
        else:
            continue
        assert event["status"] == expected_status, (event["kind"], payload)
    missing = []
    for event in events:
        if event["kind"] == "missing.recorded":
            item = event["payload"]["missing"]
            for target in item["targets"] or [{}]:
                name = target.get("symbol") or target.get("test") or ""
                missing.append(f"{item['reason']}:{name}")
    behaviour = expected["expected"].get("behaviour", [])  # E03's comparison adds these
    assert sorted(missing) == sorted(expected["expected"]["missing"] + behaviour), log
    results = [
        f"{e['payload']['invariant']}:{e['payload']['result']}"
        for e in events
        if e["kind"] == "invariant.result"
    ]
    assert sorted(results) == sorted(expected["expected"].get("invariants", [])), log
    for event in events:
        if event["kind"] == "invariant.result":  # ADR-0015 §2; it cites its own evidence first
            status = {"pass": "ok", "fail": "fail", "not_evaluated": "warn"}
            assert event["status"] == status[event["payload"]["result"]]
            assert event["payload"]["evidence_ids"][0] in evidence
    # F04: the status the rules give without claims (no review stage yet), from the log alone;
    # F05: the card the run published says the same.
    decision = decide(inputs_from_events(events))
    wanted = expected["expected"].get("deterministic_status", expected["expected"]["status"])
    assert decision.status == wanted, (decision, log)
    (card,) = [e["payload"]["card"] for e in events if e["kind"] == "card.published"]
    assert card["status"] == wanted


@pytest.mark.parametrize(
    "name",
    [
        "honest-fix", "injection", "test-tamper", "perf-regress", "noisy", "silent-change",
        "false-claim", "api-break", "new-dep", "probe-only", "probe-semantic", "policy-weaken",
        "forbidden-path", "invariants-pass", "net-call", "selection", "untested", "flaky",
        "breaks-one",
    ],
)  # fmt: skip
def test_a_run_reproduces_the_fixture(name: str, tmp_path: Path, golden: Golden) -> None:
    events = run_fixture(name, tmp_path, "process")
    check_against_expectation(name, events)
    (card,) = [e["payload"]["card"] for e in events if e["kind"] == "card.published"]
    golden.check(f"cards/{name}.json", normalised(card))  # F05: the card, as reviewed
    kinds = Counter(e["kind"] for e in events)
    assert kinds["sandbox.ready"] == 2
    assert kinds["env.provisioned"] == 2
    ready = [e["payload"] for e in events if e["kind"] == "sandbox.ready"]
    assert {r["backend"] for r in ready} == {"process"}
    assert {r["network"] for r in ready} == {"detected-only"}
    assert started_backend(events) == "process"
    if name == "policy-weaken":  # F01: base's policy in force, head's edit named
        (change,) = [
            e["payload"]["evidence"]
            for e in events
            if e["kind"] == "evidence.created"
            and e["payload"]["evidence"]["type"] == "policy_change"
        ]
        expected = tomllib.loads((FIXTURES / f"{name}.toml").read_text(encoding="utf-8"))
        assert change["data"]["weakened"] == expected["expected"]["weakened"]
        logged = [e["payload"]["message"] for e in events if e["kind"] == "log.line"]
        assert "policy (repro.yml on base): invariants no_network, forbidden_paths" in logged
    if name == "selection":  # B14: each test reaches the changed functions that selected it
        covers = {
            (e["payload"]["source"], e["payload"]["target"])
            for e in events
            if e["kind"] == "graph.edge" and e["payload"]["type"] == "covers"
        }
        assert ("tests/test_prices.py::test_net", "shop.prices:net#0") in covers, sorted(covers)
    if name == "noisy":  # E03: each traced run's observations and the comparison, stored
        assert_observations_stored(tmp_path)
    if name == "perf-regress":  # ABBA: ten timing runs on each side for the tests worth timing
        samples = Counter(
            (e["target"]["test"], e["payload"]["side"])
            for e in events
            if e["kind"] == "perf.sample"
        )
        for test in ("test_unique_tags", "test_report_is_sorted"):
            assert samples[(f"tests/test_tags.py::{test}", "base")] == 10
            assert samples[(f"tests/test_tags.py::{test}", "head")] == 10
        # H02: too fast in every traced run (never timed), or in the first ABBA block (2 a side)
        small = "tests/test_tags.py::test_unique_tags_small"
        assert samples[(small, "base")] == samples[(small, "head")] in (0, 2)
    stages = [e["stage"] for e in events if e["kind"] == "stage.started"]
    assert stages == ["build", "run", "diff", "check", "card"]  # claims (read): Stage G
    assert events[-1]["kind"] == "run.finished"
    assert events[-1]["status"] == "ok"


def assert_observations_stored(tmp_path: Path) -> None:
    from repro.runner.observations import load_observations  # noqa: PLC0415
    from repro.store.cbor import decode  # noqa: PLC0415

    env = clean_env(tmp_path, REPRO_SANDBOX="process", REPRO_RUN_ROOT=str(tmp_path / "scratch"))
    paths = load_settings(env=env).paths
    with StoreReader(paths.database) as reader:
        (run,) = reader.runs(10)
    folder = paths.runs / run.id / "obs"
    assert sorted(p.name for p in folder.iterdir()) == [
        "A.cbor",
        "A2.cbor",
        "H.cbor",
        "behaviour.cbor",
    ]
    behaviour = decode((folder / "behaviour.cbor").read_bytes())
    assert behaviour["counts"]["differences"] == 0
    for label, ref in behaviour["observations"].items():
        data = (paths.runs / run.id / ref["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == ref["sha256"]
        assert load_observations(data).label == label
        with StoreReader(paths.database) as reader:
            row = reader.artifact(run.id, ref["sha256"])
        assert row is not None


def test_test_events_count_attempts(tmp_path: Path) -> None:
    events = run_fixture("honest-fix", tmp_path, "process")
    finished = [
        (
            e["target"]["test"],
            e["payload"]["side"],
            e["payload"]["attempt"],
            e["payload"]["outcome"],
        )
        for e in events
        if e["kind"] == "test.finished"
    ]
    test = "tests/test_dates.py::test_parse_date_with_timezone"
    # Base: A, then three reruns; A2 is the repeat base run, a side of its own (base_repeat).
    assert [f[2:] for f in finished if f[0] == test and f[1] == "base"] == [
        (1, "failed"),
        (2, "failed"),
        (3, "failed"),
        (4, "failed"),
    ]
    assert [f[2:] for f in finished if f[0] == test and f[1] == "base_repeat"] == [(1, "failed")]
    # Head: H, then three reruns.
    assert [f[2:] for f in finished if f[0] == test and f[1] == "head"] == [
        (1, "passed"),
        (2, "passed"),
        (3, "passed"),
        (4, "passed"),
    ]


def test_an_environment_that_cannot_be_built_is_unverifiable(tmp_path: Path) -> None:
    """A dependency that is not on the index: provisioning fails with uv's reason, the tests
    are not attempted, and the run still completes with what it could not check."""
    spec = ALL_SPECS["honest-fix"]
    broken = type(spec)(
        name="broken-env",
        description=spec.description,
        base={
            **spec.base,
            "pyproject.toml": (
                '[project]\nname = "dates-fixture"\nversion = "0.1.0"\n'
                'dependencies = ["repro-fixture-package-that-does-not-exist==1.0"]\n'
            ),
        },
        heads=spec.heads,
    )
    events = run_spec(broken, "fix/parse-date-utc", tmp_path)
    missing = [e["payload"]["missing"] for e in events if e["kind"] == "missing.recorded"]
    assert sorted(m["detail"].split(":", 1)[0] for m in missing) == ["base", "head"]
    assert {m["reason"] for m in missing} == {"environment_failed"}
    assert all("repro-fixture-package-that-does-not-exist" in m["detail"] for m in missing)
    assert not [e for e in events if e["kind"] == "test.selected"]
    found = [e["payload"]["evidence"]["type"] for e in events if e["kind"] == "evidence.created"]
    assert found == ["policy"]  # the policy in force (F04), and nothing observed
    decision = decide(inputs_from_events(events))
    assert decision.status == "UNVERIFIABLE"
    assert [r.rule for r in decision.reasons] == ["environment_failed"]
    (card,) = [e["payload"]["card"] for e in events if e["kind"] == "card.published"]
    conditions = card["conditions"]
    assert (card["status"], conditions["python_version"], conditions["seeds"]) == (
        "UNVERIFIABLE",
        None,
        None,
    )  # F05: what the run did not reach is null, never guessed
    assert events[-1]["kind"] == "run.finished"


def test_invariants_are_evaluated_when_head_cannot_be_built(tmp_path: Path) -> None:
    """F02: head adds a dependency that does not install. The invariants that need no
    environment are still evaluated, and that dependency is still one the change adds."""
    spec = ALL_SPECS["forbidden-path"]
    head = dict(spec.heads["change/rename-table"])
    head["pyproject.toml"] = str(spec.base["pyproject.toml"]).replace(
        'version = "0.1.0"\n',
        'version = "0.1.0"\ndependencies = ["repro-fixture-package-that-does-not-exist==1.0"]\n',
    )
    policy = (
        "invariants:\n  - public_api_stable: true\n  - no_new_dependencies: true\n"
        '  - forbidden_paths: ["migrations/"]\n'
    )
    broken = type(spec)(
        name="broken-head",
        description=spec.description,
        base={**spec.base, "repro.yml": policy},
        heads={"change/rename-table": head},
    )
    events = run_spec(broken, "change/rename-table", tmp_path)
    missing = [e["payload"]["missing"] for e in events if e["kind"] == "missing.recorded"]
    assert [m["detail"].split(":", 1)[0] for m in missing if m["reason"] == "environment_failed"]
    results = {
        e["payload"]["invariant"]: e["payload"] for e in events if e["kind"] == "invariant.result"
    }
    assert {name: r["result"] for name, r in results.items()} == {
        "public_api_stable": "pass",
        "no_new_dependencies": "fail",
        "forbidden_paths": "fail",
    }
    evidence = {
        e["payload"]["evidence"]["id"]: e["payload"]["evidence"]
        for e in events
        if e["kind"] == "evidence.created"
    }
    cited = [evidence[i] for i in results["no_new_dependencies"]["evidence_ids"][1:]]
    assert [(c["type"], c["data"]["package"], c["data"]["source"]) for c in cited] == [
        ("dep_diff", "repro-fixture-package-that-does-not-exist", "declared")
    ]
    assert events[-1]["kind"] == "run.finished"


def run_spec(spec: Any, head_ref: str, tmp_path: Path) -> list[dict[str, Any]]:
    """``repro run`` on a fixture built from ``spec`` (process sandbox): its events."""
    built = build_repo(spec, tmp_path / "repo")
    env = clean_env(tmp_path, REPRO_SANDBOX="process", REPRO_RUN_ROOT=str(tmp_path / "scratch"))
    head = built.head_shas[head_ref]
    argv = ["run", "--repo", str(built.path), "--base", built.base_sha, "--head", head]
    result = runner.invoke(app, [*argv, "--no-serve"], env=env)
    assert result.exit_code == 0, result.output
    with StoreReader(load_settings(env=env).paths.database) as reader:
        (run,) = reader.runs(10)
        events = [json.loads(event.body) for event in reader.events(run.id, 0, 100_000)]
        check_card(events, reader.card(run.id))
    return events


docker = pytest.mark.skipif(
    not docker_available() and os.environ.get("REPRO_REQUIRE_DOCKER") != "1",
    reason="Docker with Linux containers is not available",
)


@docker
def test_a_run_in_containers(tmp_path: Path) -> None:
    events = run_fixture("test-tamper", tmp_path, "docker")
    check_against_expectation("test-tamper", events)
    ready = [e["payload"] for e in events if e["kind"] == "sandbox.ready"]
    assert {(r["backend"], r["isolation_level"], r["network"]) for r in ready} == {
        ("docker", "L3", "enforced")
    }
    assert started_backend(events) == "docker"


@pytest.mark.skipif(
    os.environ.get("REPRO_DOCKER_RUNTIME") != "runsc",
    reason="gVisor's runtime is not in use (CI's gVisor job)",
)
def test_a_run_under_gvisor_is_l4(tmp_path: Path) -> None:
    """H03: the same fixture, end to end, with the containers under gVisor: the same evidence,
    and the card states the layer it added."""
    events = run_fixture("test-tamper", tmp_path, "docker", REPRO_DOCKER_RUNTIME="runsc")
    check_against_expectation("test-tamper", events)
    ready = [e["payload"] for e in events if e["kind"] == "sandbox.ready"]
    assert {(r["isolation_level"], r["network"]) for r in ready} == {("L4", "enforced")}
    assert started_backend(events) == "gvisor"
    (card,) = [e["payload"]["card"] for e in events if e["kind"] == "card.published"]
    assert card["conditions"]["isolation_level"] == "L4"


@pytest.mark.skipif(
    os.environ.get("REPRO_DOCKER_RUNTIME") != "kata",
    reason="Kata's runtime is not in use (a Linux host with KVM: scripts/kata/setup.sh)",
)
def test_a_run_under_kata_is_l5(tmp_path: Path) -> None:
    """K10: the same fixture, end to end, each container in a microVM of Kata's: the same
    evidence, and the card states the layer the probe saw, a kernel of each container's own."""
    events = run_fixture("test-tamper", tmp_path, "docker", REPRO_DOCKER_RUNTIME="kata")
    check_against_expectation("test-tamper", events)
    ready = [e["payload"] for e in events if e["kind"] == "sandbox.ready"]
    assert {(r["isolation_level"], r["network"]) for r in ready} == {("L5", "enforced")}
    assert started_backend(events) == "kata"
    (card,) = [e["payload"]["card"] for e in events if e["kind"] == "card.published"]
    assert card["conditions"]["isolation_level"] == "L5"


#: K09: E2B's tests run where a self-hosted E2B answers: its SDK's variables set (E2B_API_KEY,
#: and E2B_API_URL or E2B_DOMAIN) and REPRO_E2B_LIVE=1 (CI's e2b job, or a host with KVM).
e2b_live = pytest.mark.skipif(
    os.environ.get("REPRO_E2B_LIVE") != "1",
    reason="no self-hosted E2B to reach (REPRO_E2B_LIVE=1, E2B_API_KEY, E2B_API_URL)",
)


@e2b_live
def test_a_run_in_e2b_is_l5(tmp_path: Path) -> None:
    """K09: the same fixture, end to end, each side in a Firecracker microVM of E2B's, sealed
    by its own kernel's firewall: the same evidence, and the card at L5, its network enforced."""
    memory = os.environ.get("REPRO_E2B_MEMORY_MB", "512")
    events = run_fixture("test-tamper", tmp_path, "e2b", REPRO_E2B_MEMORY_MB=memory)
    check_against_expectation("test-tamper", events)
    ready = [e["payload"] for e in events if e["kind"] == "sandbox.ready"]
    assert {(r["backend"], r["isolation_level"], r["network"]) for r in ready} == {
        ("e2b", "L5", "enforced")
    }
    assert started_backend(events) == "e2b"
    (card,) = [e["payload"]["card"] for e in events if e["kind"] == "card.published"]
    assert card["conditions"]["isolation_level"] == "L5"


@docker
def test_no_network_rests_on_enforcement_in_containers(tmp_path: Path) -> None:
    """F03: where the sandbox has no network, no_network's pass says so, and no
    ``network_not_enforced`` item is needed (the process sandbox's pass carries one)."""
    events = run_fixture("invariants-pass", tmp_path, "docker")
    results = {
        e["payload"]["invariant"]: e["payload"] for e in events if e["kind"] == "invariant.result"
    }
    assert {name: r["result"] for name, r in results.items()} == dict.fromkeys(
        (
            "public_api_stable",
            "no_new_dependencies",
            "no_network",
            "perf_budget",
            "forbidden_paths",
        ),
        "pass",
    )
    evidence = {
        e["payload"]["evidence"]["id"]: e["payload"]["evidence"]
        for e in events
        if e["kind"] == "evidence.created"
    }
    no_network = evidence[results["no_network"]["evidence_ids"][0]]
    assert no_network["data"]["details"] == {"network": "enforced"}
    reasons = {e["payload"]["missing"]["reason"] for e in events if e["kind"] == "missing.recorded"}
    assert "network_not_enforced" not in reasons
