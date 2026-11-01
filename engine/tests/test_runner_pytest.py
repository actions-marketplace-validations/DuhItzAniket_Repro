"""The pytest command Repro runs, and reading the plugin's results as untrusted input (D04)."""

import json

import pytest

from repro.runner import plugin_env, pytest_argv, read_results
from repro.runner import pytest as runner


def lines(*records: object) -> bytes:
    return b"".join(
        (record if isinstance(record, bytes) else json.dumps(record).encode()) + b"\n"
        for record in records
    )


TEST = {"kind": "test", "nodeid": "t.py::a", "outcome": "passed", "duration_s": 0.5}
SESSION = {
    "kind": "session",
    "exitstatus": 0,
    "collected": 1,
    "pytest": "9.1.1",
    "python": "3.13.13",
    "seed": 0,
}


def test_the_command_overrides_the_project() -> None:
    argv = pytest_argv("py", out_dir="/out", tests=["t.py::a"])
    assert argv[:3] == ["py", "-m", "pytest"]
    joined = " ".join(argv)
    for option in (
        "-o addopts=",
        "-p repro_pytest",
        "-p no:randomly",
        "-p no:xdist",
        "-p no:cacheprovider",
        "--continue-on-collection-errors",
        "--junitxml /out/junit.xml",
    ):
        assert option in joined
    assert argv[-1] == "t.py::a"


def test_the_environment() -> None:
    assert plugin_env("/out", seed=7) == {
        "REPRO_OUT": "/out",
        "REPRO_SEED": "7",
        "PYTHONUTF8": "1",
        "TZ": "UTC",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "REPRO_NET_GUARD": "1",
    }
    assert "REPRO_NET_GUARD" not in plugin_env("/out", net_guard=False)


def test_a_complete_run() -> None:
    error = {"kind": "collect_error", "nodeid": "u.py", "message": "ImportError"}
    run = read_results(lines(TEST, error, SESSION))
    assert run.complete
    assert run.outcomes() == {"t.py::a": "passed"}
    assert run.tests["t.py::a"].duration_s == 0.5
    assert [(e.nodeid, e.message) for e in run.collect_errors] == [("u.py", "ImportError")]
    assert run.session == {k: v for k, v in SESSION.items() if k != "kind"}
    assert (run.malformed, run.notes) == (0, ())


def test_nothing_written() -> None:
    run = read_results(None)
    assert not run.complete
    assert run.notes == ("the plugin wrote no results",)


def test_no_session_record_is_incomplete() -> None:
    run = read_results(lines(TEST))
    assert not run.complete
    assert run.outcomes() == {"t.py::a": "passed"}
    assert run.notes == ("the run did not finish: no session record",)


@pytest.mark.parametrize(
    "record",
    [
        b"not json",
        b"[1, 2]",
        b"{}",
        {"kind": "what"},
        {"kind": "test", "nodeid": "t.py::b", "outcome": "won"},
        {"kind": "test", "nodeid": "", "outcome": "passed"},
        {"kind": "test", "nodeid": 3, "outcome": "passed"},
        {"kind": "test", "outcome": "passed"},
        {"kind": "test", "nodeid": "t.py::b", "outcome": "passed", "duration_s": "slow"},
        {"kind": "collect_error", "nodeid": "u.py"},
        {"kind": "collected", "nodeid": "t.py::a[1]", "test": None, "case": "not a digest"},
        {"kind": "session", "exitstatus": "0", "collected": 1},
        {"kind": "session", "exitstatus": 0},
    ],
)
def test_a_malformed_record_is_counted_and_skipped(record: object) -> None:
    run = read_results(lines(TEST, record, SESSION))
    assert run.malformed == 1
    assert run.outcomes() == {"t.py::a": "passed"}
    assert run.complete


def test_text_is_bounded_and_phases_are_known() -> None:
    record = {
        "kind": "test",
        "nodeid": "t.py::" + "n" * 5000,
        "outcome": "failed",
        "phase": "elsewhere",
        "message": "m" * 5000,
    }
    result = next(iter(read_results(lines(record)).tests.values()))
    assert len(result.nodeid) == 2000
    assert result.message is not None
    assert len(result.message) == 2000
    assert result.phase is None


def test_records_past_the_limit_are_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "MAX_RECORDS", 2)
    run = read_results(lines(TEST, TEST, SESSION))
    assert not run.complete
    assert "more than 2 records: the rest were not read" in run.notes


def test_the_last_record_of_a_rerun_test_counts() -> None:
    failed = {**TEST, "outcome": "failed"}
    assert read_results(lines(failed, TEST, SESSION)).outcomes() == {"t.py::a": "passed"}


def test_a_case_digest_is_read_with_its_test() -> None:
    """The plugin's digest of a parametrized case's own parameters (testdiff's kept cases)."""
    collected = [
        {"kind": "collected", "nodeid": "t.py::p[1]", "test": ["t.py", "p", 3], "case": "a" * 32},
        {"kind": "collected", "nodeid": "t.py::p[x]", "test": ["t.py", "p", 3]},
        {"kind": "collected", "nodeid": "t.py::a", "test": ["t.py", "a", 1]},
    ]
    run = read_results(lines(*collected, TEST, SESSION))
    assert run.malformed == 0
    assert run.cases == {"t.py::p[1]": "a" * 32}
    assert set(run.collected) == {"t.py::p[1]", "t.py::p[x]", "t.py::a"}
