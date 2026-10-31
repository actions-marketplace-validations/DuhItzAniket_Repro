"""Test selection on the fixture repositories, end to end (D05).

Roadmap D05: "on fixtures, selected tests ⊇ the tests a human expects. Uncovered changed symbols
are always listed." Each fixture's expectation file names the tests a person expects, with their
reasons, and the changed symbols no test runs; the selection must match them exactly. Base is
mapped (every test, in the process sandbox), head's tests are listed, the rule picks, and the
selected tests then run mapped on head to decide coverage.
"""

import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from fixtures.repo_builder import build_repo
from fixtures.specs import ALL_SPECS, HONEST_FIX, SELECTION
from repro.analysis import analyse
from repro.gitops import Git, snapshot
from repro.provision import DockerProvisioner, Environment, HostProvisioner, inspect_project
from repro.runner import run_tests, selection
from repro.sandbox import DockerSandbox, Limits, ProcessSandbox, docker_available
from repro.sandbox.image import sandbox_image

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class Side:
    box: ProcessSandbox
    env: Environment


@dataclass
class Outcome:
    selected: tuple[selection.Selected, ...]
    gaps: tuple[str, ...]
    base: selection.TestMap
    base_outcomes: dict[str, str]


@pytest.fixture(scope="module")
def provisioner(tmp_path_factory: pytest.TempPathFactory) -> HostProvisioner:
    return HostProvisioner(tmp_path_factory.mktemp("envs"))


def prepare(git: Git, sha: str, side: str, root: Path, provisioner: HostProvisioner) -> Side:
    tree = snapshot(git, sha, root / side / "snapshot")
    box = ProcessSandbox(root / side / "work", Limits(timeout_s=300))
    return Side(box, provisioner.provision(side, inspect_project(tree.root), box))


def run_selection(name: str, root: Path, provisioner: HostProvisioner) -> Outcome:
    spec = ALL_SPECS[name]
    head_ref = tomllib.loads((FIXTURES / f"{name}.toml").read_text(encoding="utf-8"))
    built = build_repo(spec, root / "repo")
    git = Git(built.path)
    base_sha, head_sha = built.base_sha, built.head_shas[head_ref["fixture"]["head"]]
    analysis = analyse(git, base_sha, head_sha, root / "analysis")
    changes = analysis.changes.changes
    base, head = (prepare(git, sha, side, root, provisioner) for side, sha in
                  (("base", base_sha), ("head", head_sha)))  # fmt: skip
    resolve_base, resolve_head = selection.side_resolvers(git, base_sha, head_sha)
    mapped = run_tests(base.box, base.env, map_tests=True)
    assert mapped.results.complete, mapped.execution.stdout.decode(errors="replace")
    base_map = selection.test_map(mapped.results, resolve_base)
    listed = run_tests(head.box, head.env, collect_only=True)
    assert listed.results.complete, listed.execution.stdout.decode(errors="replace")
    head_tests = selection.test_map(listed.results, resolve_head)
    chosen = selection.select(changes, base_map, head_tests, (analysis.base, analysis.head))
    head_map = selection.TestMap({}, {})
    if chosen:
        ran = run_tests(head.box, head.env, tests=[s.test for s in chosen], map_tests=True)
        assert ran.results.complete, ran.execution.stdout.decode(errors="replace")
        head_map = selection.test_map(ran.results, resolve_head)
    gaps = selection.uncovered(changes, chosen, base_map, head_map)
    return Outcome(chosen, tuple(g.symbol_id for g in gaps), base_map, mapped.results.outcomes())


@pytest.mark.parametrize("name", ["selection", "untested", "generated"])
def test_the_selection_is_what_a_person_expects(
    name: str, tmp_path: Path, provisioner: HostProvisioner
) -> None:
    expected = tomllib.loads((FIXTURES / f"{name}.toml").read_text(encoding="utf-8"))
    outcome = run_selection(name, tmp_path, provisioner)
    assert {s.test: list(s.reasons) for s in outcome.selected} == expected["selection"].get(
        "selected", {}
    )
    assert sorted(outcome.gaps) == sorted(expected["selection"]["uncovered"])
    gaps = [item for item in expected["expected"]["missing"] if item.startswith("no_covering")]
    assert sorted(f"no_covering_test:{gap}" for gap in outcome.gaps) == sorted(gaps)
    assert set(outcome.base_outcomes.values()) == {"passed"}  # every base test ran, and passed


def test_why_each_test_was_selected(tmp_path: Path, provisioner: HostProvisioner) -> None:
    outcome = run_selection("selection", tmp_path, provisioner)
    symbols = {s.test: s.symbols for s in outcome.selected}
    assert symbols["tests/test_cart.py::test_total"] == ("shop.prices:net#0",)  # via gross()
    assert symbols["tests/test_cart.py::test_names"] == (
        "shop.cart:Cart.names#0",
        "test_cart:test_names#0",
    )
    assert symbols["tests/test_prices.py::test_discount_is_clamped"] == (
        "test_prices:test_discount_is_clamped#0",
    )
    # The base map is precise: the empty cart's total never reaches gross() or net().
    ran = outcome.base.ran["tests/test_cart.py::test_empty_total"]
    assert {"shop.cart:Cart.__init__#0", "shop.cart:Cart.total#0"} <= ran
    assert "shop.prices:net#0" not in ran
    # Fixtures from conftest.py are part of the tests that use them.
    assert "conftest:cart#0" in outcome.base.ran["tests/test_cart.py::test_total"]


def test_honest_fix_selects_both_tests_of_the_fixed_function(
    tmp_path: Path, provisioner: HostProvisioner
) -> None:
    built = build_repo(HONEST_FIX, tmp_path / "repo")
    git = Git(built.path)
    head_sha = built.head_shas["fix/parse-date-utc"]
    analysis = analyse(git, built.base_sha, head_sha, tmp_path / "analysis")
    base = prepare(git, built.base_sha, "base", tmp_path, provisioner)
    head = prepare(git, head_sha, "head", tmp_path, provisioner)
    resolve_base, resolve_head = selection.side_resolvers(git, built.base_sha, head_sha)
    mapped = run_tests(base.box, base.env, map_tests=True)
    base_map = selection.test_map(mapped.results, resolve_base)
    head_tests = selection.test_map(
        run_tests(head.box, head.env, collect_only=True).results, resolve_head
    )
    chosen = selection.select(
        analysis.changes.changes, base_map, head_tests, (analysis.base, analysis.head)
    )
    # Both tests ran parse_date; the fix also changed the module's imports (``UTC``), which the
    # test module imports.
    reasons, symbols = (
        ("blast_radius", "module_import"),
        ("pkg.dates:<module>#0", "pkg.dates:parse_date#0"),
    )
    assert [(s.test, s.reasons, s.symbols) for s in chosen] == [
        ("tests/test_dates.py::test_parse_date_naive_input_is_utc", reasons, symbols),
        ("tests/test_dates.py::test_parse_date_with_timezone", reasons, symbols),
    ]
    # The map changes nothing about the run: the same outcomes without it.
    assert run_tests(base.box, base.env).results.outcomes() == mapped.results.outcomes()


def test_the_map_cache_round_trip(tmp_path: Path, provisioner: HostProvisioner) -> None:
    built = build_repo(HONEST_FIX, tmp_path / "repo")
    git = Git(built.path)
    base = prepare(git, built.base_sha, "base", tmp_path, provisioner)
    resolve_base, _ = selection.side_resolvers(git, built.base_sha, built.base_sha)
    made = selection.test_map(run_tests(base.box, base.env, map_tests=True).results, resolve_base)
    cache = selection.MapCache(tmp_path / "maps")
    key = selection.map_key(
        built.base_sha, base.env.project.lock_sha256, base.env.python_version, "host"
    )
    cache.put(key, made)
    assert cache.get(key) == made


def test_no_free_tool_id_is_reported(tmp_path: Path, provisioner: HostProvisioner) -> None:
    """Another tool holds every ID Repro may take: the map is reported unavailable, and the
    tests still run."""
    built = build_repo(HONEST_FIX, tmp_path / "repo")
    base = prepare(Git(built.path), built.base_sha, "base", tmp_path, provisioner)
    shutil.copyfile(FIXTURES / "busy_conftest.py", base.env.project.root / "conftest.py")
    run = run_tests(base.box, base.env, map_tests=True)
    assert run.results.map_unavailable == "no sys.monitoring tool ID is free"
    assert run.results.maps == {}
    assert len(run.results.outcomes()) == 2


docker = pytest.mark.skipif(
    not docker_available() and os.environ.get("REPRO_REQUIRE_DOCKER") != "1",
    reason="Docker with Linux containers is not available",
)


@docker
def test_the_map_is_the_same_in_a_container(tmp_path: Path, provisioner: HostProvisioner) -> None:
    """Places are relative to the snapshot, so a container's map (L3) names the same symbols."""
    built = build_repo(SELECTION, tmp_path / "repo")
    git = Git(built.path)
    resolve_base, _ = selection.side_resolvers(git, built.base_sha, built.base_sha)
    host = prepare(git, built.base_sha, "base", tmp_path, provisioner)
    on_host = selection.test_map(
        run_tests(host.box, host.env, map_tests=True).results, resolve_base
    )
    image = sandbox_image()
    with DockerSandbox(
        tmp_path / "docker", git, built.base_sha, image=image, limits=Limits(timeout_s=300)
    ) as box:
        env = DockerProvisioner(image).provision(
            "base", inspect_project(host.env.project.root), box
        )
        in_container = selection.test_map(run_tests(box, env, map_tests=True).results, resolve_base)
    assert in_container.ran == on_host.ran
    assert in_container.tests == on_host.tests
    assert len(on_host.ran) == 6
