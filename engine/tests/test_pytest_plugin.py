"""Repro's pytest plugin (D04), run for real: in the process sandbox on Python 3.12 to 3.15, and
in a container.

Roadmap D04: "the plugin's own test matrix passes on 3.12, 3.13, 3.14 and 3.15. The same test
run twice gives the same outcomes." The suite (``data/plugin_suite``) has a test for every
outcome and phase, random draws, the network guard, a module that fails to import, and a
``pytest.ini`` whose ``addopts = -x`` would stop the run at its first failure were Repro's
``-o addopts=`` not applied, and whose ``-m "not stress"`` must be kept (H02): its stress test
fails if it runs. Its requirements pin pytest 9.1.1 and every dependency, so each Python gets
the same wheels from the package index.
"""

import os
import re
import shutil
from pathlib import Path

import pytest

from gitrepo import Repo
from repro import runner
from repro.gitops import Git
from repro.provision import DockerProvisioner, Environment, HostProvisioner, inspect_project
from repro.runner import run_tests
from repro.sandbox import DockerSandbox, Limits, ProcessSandbox, docker_available
from repro.sandbox.image import sandbox_image

SUITE = Path(__file__).parent / "data" / "plugin_suite"
PROJECTS = Path(__file__).parent / "data" / "projects"
PYTHONS = ("3.12", "3.13", "3.14", "3.15")
OUTCOMES = "tests/test_outcomes.py::"
EXPECTED = {
    f"{OUTCOMES}test_passes": "passed",
    f"{OUTCOMES}test_fails": "failed",
    f"{OUTCOMES}test_skipped_by_marker": "skipped",
    f"{OUTCOMES}test_skips_itself": "skipped",
    f"{OUTCOMES}test_xfails": "xfailed",
    f"{OUTCOMES}test_xpasses": "xpassed",
    f"{OUTCOMES}test_strict_xpass_fails": "failed",
    f"{OUTCOMES}test_setup_error": "error",
    f"{OUTCOMES}test_teardown_error": "error",
    f"{OUTCOMES}test_failure_outranks_teardown_error": "failed",
    f"{OUTCOMES}test_parametrized[1]": "passed",
    f"{OUTCOMES}test_parametrized[2]": "failed",
    "tests/test_random.py::test_draws_a_lot": "passed",
    "tests/test_random.py::test_reports_its_draw": "failed",
    "tests/test_network.py::test_name_resolution_is_refused": "passed",
    "tests/test_network.py::test_outside_connection_is_refused": "passed",
    "tests/test_network.py::test_localhost_resolves": "passed",
    "tests/test_network.py::test_loopback_still_works": "passed",
    "tests/test_pinned.py::test_the_run_is_pinned": "passed",
}
DRAW = "tests/test_random.py::test_reports_its_draw"


class Side:
    """A copy of the suite, provisioned with one Python, in a process sandbox of its own."""

    def __init__(self, provisioner: HostProvisioner, root: Path) -> None:
        self.snapshot = root / "snapshot"
        shutil.copytree(SUITE, self.snapshot)
        self.box = ProcessSandbox(root / "work", Limits(timeout_s=300))
        self.env: Environment = provisioner.provision(
            "base", inspect_project(self.snapshot), self.box
        )

    def run(self, *tests: str, seed: int = 0, timeout_s: float | None = None) -> runner.TestRun:
        return run_tests(self.box, self.env, tests=tests, seed=seed, timeout_s=timeout_s)


@pytest.fixture(scope="module")
def cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("envs")


@pytest.fixture(scope="module")
def side(cache: Path, tmp_path_factory: pytest.TempPathFactory) -> Side:
    return Side(HostProvisioner(cache), tmp_path_factory.mktemp("side"))


def draw(run: runner.TestRun) -> str:
    message = run.results.tests[DRAW].message or ""
    found = re.search(r"draw=([0-9.e-]+)", message)
    assert found, message
    return found.group(1)


def check_suite(run: runner.TestRun, python: str) -> None:
    """Every test's outcome, where it ended and why, and the session's report."""
    output = run.execution.stdout.decode(errors="replace") + run.execution.stderr.decode(
        errors="replace"
    )
    results = run.results
    assert results.complete, output
    assert results.malformed == 0
    assert results.outcomes() == EXPECTED, output
    tests = results.tests
    assert tests[f"{OUTCOMES}test_setup_error"].phase == "setup"
    assert "setup broke" in (tests[f"{OUTCOMES}test_setup_error"].message or "")
    assert tests[f"{OUTCOMES}test_teardown_error"].phase == "teardown"
    assert "teardown broke" in (tests[f"{OUTCOMES}test_teardown_error"].message or "")
    assert tests[f"{OUTCOMES}test_failure_outranks_teardown_error"].phase == "call"
    assert "arithmetic is off" in (tests[f"{OUTCOMES}test_fails"].message or "")
    assert tests[f"{OUTCOMES}test_passes"].phase is None
    assert tests[f"{OUTCOMES}test_passes"].message is None
    # Each parametrized case's digest of its own parameters, and only theirs.
    cases = results.cases
    assert set(cases) == {f"{OUTCOMES}test_parametrized[1]", f"{OUTCOMES}test_parametrized[2]"}
    assert cases[f"{OUTCOMES}test_parametrized[1]"] != cases[f"{OUTCOMES}test_parametrized[2]"]
    assert [error.nodeid for error in results.collect_errors] == ["tests/test_broken_import.py"]
    assert "a_module_that_does_not_exist" in results.collect_errors[0].message
    session = results.session
    assert session is not None
    assert session["exitstatus"] == 1  # tests failed; the collection error did not stop the run
    assert session["pytest"] == "9.1.1"
    assert session["python"].startswith(f"{python}.")
    assert run.execution.returncode == 1
    # JUnit is kept for people and other tools, not read: it lists a teardown error as a test
    # case of its own, beside the test's own entry.
    junit = run.junit or b""
    assert junit.startswith(b"<?xml")
    assert b'name="test_reports_its_draw"' in junit


@pytest.mark.parametrize("python", PYTHONS)
def test_the_suite_on_each_python(
    cache: Path, tmp_path: Path, python: str, request: pytest.FixtureRequest
) -> None:
    """Roadmap D04: the plugin's own test matrix passes on 3.12, 3.13, 3.14 and 3.15."""
    this = side_for(request, cache, tmp_path, python)
    run = this.run()
    check_suite(run, python)
    attempts = {d.detail for d in run.execution.detections.items if d.kind == "network"}
    assert {"example.com:443", "192.0.2.1:9"} <= attempts  # refused, and recorded
    files = [d.detail for d in run.execution.detections.items if d.kind == "file"]
    assert files == []  # the results and JUnit files are where Repro expects them


def side_for(request: pytest.FixtureRequest, cache: Path, root: Path, python: str) -> Side:
    if python == "3.13":
        side: Side = request.getfixturevalue("side")
        return side
    return Side(HostProvisioner(cache, python=python), root)


def test_the_same_run_twice_gives_the_same_outcomes(side: Side) -> None:
    """Roadmap D04: the same test run twice gives the same outcomes, and the same draws."""
    first, second = side.run(), side.run()
    check_suite(first, "3.13")
    assert second.results.outcomes() == first.results.outcomes()
    assert draw(second) == draw(first)
    assert [e.nodeid for e in second.results.collect_errors] == ["tests/test_broken_import.py"]


def test_a_draw_does_not_depend_on_what_ran_before(side: Side) -> None:
    alone = side.run(DRAW)
    assert alone.results.outcomes() == {DRAW: "failed"}
    assert alone.results.session is not None
    assert alone.results.session["collected"] == 1
    assert draw(alone) == draw(side.run())


def test_the_seed_decides_the_draws(side: Side) -> None:
    assert draw(side.run(DRAW, seed=1)) == draw(side.run(DRAW, seed=1))
    assert draw(side.run(DRAW, seed=1)) != draw(side.run(DRAW, seed=0))


def test_a_run_that_dies_is_incomplete_not_passing(side: Side) -> None:
    """The process ends mid-run: what was written stays, and the run says it did not finish."""
    dies = side.snapshot / "tests" / "test_zz_dies.py"
    dies.write_text(
        "import os\n\ndef test_dies():\n    os._exit(3)\n\ndef test_never_runs():\n    pass\n",
        encoding="utf-8",
    )
    try:
        run = side.run(f"{OUTCOMES}test_passes", "tests/test_zz_dies.py")
    finally:
        dies.unlink()
    assert run.execution.returncode == 3
    assert not run.results.complete
    assert run.results.outcomes() == {f"{OUTCOMES}test_passes": "passed"}
    assert "the run did not finish: no session record" in run.results.notes


def test_a_run_killed_for_its_timeout_is_incomplete(side: Side) -> None:
    hangs = side.snapshot / "tests" / "test_zz_hangs.py"
    hangs.write_text("import time\n\ndef test_hangs():\n    time.sleep(600)\n", encoding="utf-8")
    try:
        run = side.run("tests/test_zz_hangs.py", timeout_s=20)
    finally:
        hangs.unlink()
    assert run.execution.outcome == "timeout"
    assert not run.results.complete
    assert run.results.tests == {}


def test_an_older_pytest(cache: Path, tmp_path: Path) -> None:
    """A target's own pytest runs the plugin: D03's requirements fixture pins pytest 8.3.3."""
    snapshot = tmp_path / "snapshot"
    shutil.copytree(PROJECTS / "requirements", snapshot)
    box = ProcessSandbox(tmp_path / "work", Limits(timeout_s=300))
    provisioner = HostProvisioner(cache, exclude_newer="2025-01-01T00:00:00Z")
    run = run_tests(box, provisioner.provision("base", inspect_project(snapshot), box))
    assert run.results.complete, run.execution.stdout.decode(errors="replace")
    assert run.results.outcomes() == {
        "tests/test_calc.py::test_add": "passed",
        "tests/test_calc.py::test_a_dependency_is_installed": "passed",
    }
    assert run.results.session is not None
    assert run.results.session["pytest"] == "8.3.3"


docker = pytest.mark.skipif(
    not docker_available() and os.environ.get("REPRO_REQUIRE_DOCKER") != "1",
    reason="Docker with Linux containers is not available",
)


@docker
def test_the_suite_in_a_container(tmp_path: Path) -> None:
    """The same suite at L3, in Repro's image: the same outcomes, the network gone entirely."""
    repo = Repo(tmp_path / "repo")
    files = {
        path.relative_to(SUITE).as_posix(): path.read_text(encoding="utf-8")
        for path in SUITE.rglob("*")
        if path.is_file()
    }
    git, sha = Git(repo.path), repo.commit(files)
    image = sandbox_image()
    with DockerSandbox(
        tmp_path / "work", git, sha, image=image, limits=Limits(timeout_s=300)
    ) as box:
        env = DockerProvisioner(image).provision("head", inspect_project(SUITE), box)
        first = run_tests(box, env)
        second = run_tests(box, env)
    check_suite(first, "3.13")
    assert second.results.outcomes() == first.results.outcomes()
    assert draw(second) == draw(first)


def test_tests_too_many_for_a_command_line_go_in_a_file(
    side: Side, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J04: 889 node IDs passed Windows' 32,767-character command line. Past the limit the IDs
    go in a file the plugin reads: exactly those tests run, in the order given."""
    monkeypatch.setattr(runner.pytest, "ARGV_IDS_MAX", 1)
    wanted = [f"{OUTCOMES}test_passes", f"{OUTCOMES}test_fails", DRAW]
    for order in (wanted, wanted[::-1]):
        run = side.run(*order)
        assert list(run.results.tests) == order
        assert run.results.outcomes() == {test: EXPECTED[test] for test in order}
        files = [d.detail for d in run.execution.detections.items if d.kind == "file"]
        assert files == []  # the list is read from where Repro wrote it


#: A parametrized test as base has it, and as head has it with a case added and one changed.
#: pytest names a case whose parameters are not scalars by its index (``given2-expected2``),
#: so the new case comes after those: before them, it would rename them.
CASES_BASE = """import pytest


class Opaque:
    pass


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("1O", 10),
        (2.5, None),
        ((1, "a"), {"k": {3, 1}}),
        (ValueError, b"x"),
        pytest.param("O", 0, marks=pytest.mark.xfail(reason="later"), id="marked"),
        pytest.param(Opaque(), 1, id="opaque"),
        pytest.param("same", 1, id="changed"),
    ],
)
def test_cases(given, expected):
    pass
"""
CASES_HEAD = (
    CASES_BASE.replace('(ValueError, b"x"),\n', '(ValueError, b"x"),\n        ("O1", 1),\n')
    .replace('pytest.param("same", 1', 'pytest.param("same", 2')
    .replace('{"k": {3, 1}}', '{"k": {1, 3}}')
)
#: Only a case's mark changed.
CASES_MARK = CASES_BASE.replace('reason="later"', 'reason="soon"')


def test_a_case_digest_is_of_its_own_parameters(side: Side) -> None:
    """A case keeps its digest when other cases are added, and changes it when its own
    parameters or marks change; a value that is not plain has none."""
    path = side.snapshot / "tests" / "test_zz_cases.py"
    digests = []
    try:
        for source in (CASES_BASE, CASES_HEAD, CASES_MARK):
            path.write_text(source, encoding="utf-8")
            run = side.run("tests/test_zz_cases.py")
            assert run.results.complete, run.execution.stdout.decode(errors="replace")
            digests.append(run.results.cases)
    finally:
        path.unlink()
    base, head, mark = digests
    name = "tests/test_zz_cases.py::test_cases"
    kept = [
        f"{name}[{i}]" for i in ("1O-10", "2.5-None", "given2-expected2", "ValueError-x", "marked")
    ]
    assert sorted(set(base)) == sorted([*kept, f"{name}[changed]"])
    assert len(set(base.values())) == len(base)
    assert {test: head[test] for test in kept} == {test: base[test] for test in kept}
    assert head[f"{name}[changed]"] != base[f"{name}[changed]"]
    assert f"{name}[O1-1]" in head
    assert f"{name}[opaque]" not in head
    assert mark[f"{name}[marked]"] != base[f"{name}[marked]"]
    assert mark[f"{name}[1O-10]"] == base[f"{name}[1O-10]"]
