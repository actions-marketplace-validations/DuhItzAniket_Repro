"""Running a side's tests under Repro's pytest plugin (D04), and reading what it wrote.

The command is Repro's, whatever the project configures: ``-o addopts=`` drops the project's
own default options, except those that select which tests run (``-m``, ``-k``, ignores;
``repro.runner.pytest_config``, H02), ``-p no:randomly`` and ``-p no:xdist`` keep order and
process layout
fixed, ``-p no:cacheprovider`` keeps the snapshot unwritten, and ``-p repro_pytest`` loads the
plugin, which the sandboxes already put first on ``PYTHONPATH`` beside the audit hook.
``--continue-on-collection-errors`` keeps one module that fails to import from hiding every
other test's outcome. JUnit XML is written too, as an artifact for people and other tools.

The environment adds ``PYTHONUTF8=1``, ``TZ=UTC`` and the ``C.UTF-8`` locale to the
sandbox's own (fixed hash seed, no host secrets), ``REPRO_SEED`` for the plugin's per-test
random seeds, and ``REPRO_NET_GUARD=1`` so a test that needs the network fails at once, and the
same way on every isolation level.

With ``map_tests`` (D05) the plugin also records the functions each test runs, as *places*
(file relative to the snapshot, qualified name, first line); ``collect_only`` lists the tests
and where their functions are without running them. With ``trace`` (E01) it records every call
of the target functions named (``TraceTarget``): argument and result digests, per test.

What the plugin wrote comes from inside the target's process, so it is read as untrusted: a
record that is not well formed is counted and skipped, and a run with no session record (a
crash, a kill, a timeout) is incomplete, never passing.
"""

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from repro.provision.env import Environment
from repro.runner.pytest_config import selection
from repro.sandbox.process import Execution, ProcessSandbox, read_head
from repro.sandbox.remote import WORK, RemoteSandbox

TestOutcome = Literal["passed", "failed", "skipped", "xfailed", "xpassed", "error"]
OUTCOMES: Final[frozenset[str]] = frozenset(
    {"passed", "failed", "skipped", "xfailed", "xpassed", "error"}
)
PLUGIN: Final = "repro_pytest"
RESULTS: Final = "results.jsonl"
JUNIT: Final = "junit.xml"
#: At most this much of a results or JUnit file is read (a hostile run could write without end).
MAX_BYTES: Final = 64 * 1024 * 1024
MAX_RECORDS: Final = 200_000
#: The trace's own file (E03): the plugin stops writing it at ``MAX_BYTES``.
CALLS: Final = "calls.jsonl"
MAX_CALLS: Final = 500_000
#: At most this many places are read for one test's map.
MAX_PLACES: Final = 50_000
_TEXT: Final = 2000

#: A function: its file relative to the snapshot (POSIX), its qualified name, its first line.
Place = tuple[str, str, int]
#: A function to trace: file, qualified name, and the first and last lines of its symbol.
TraceTarget = tuple[str, str, int, int]
TRACE_TARGETS: Final = "trace-targets.json"
PREVIEW_CALLS: Final = "preview-calls.json"
#: The tests to run, when their IDs are too long for a command line (J04: 889 IDs passed
#: Windows' 32,767 characters). The plugin keeps those items, in that order.
SELECT: Final = "select.json"
#: The most characters of node IDs passed as arguments.
ARGV_IDS_MAX: Final = 8_000
#: A preview's length at most (the event schema's ``Preview``).
PREVIEW: Final = 512
#: At most this many calls are previewed in one run.
MAX_PREVIEWS: Final = 2000
#: A call to preview: the test, the target, the arguments' digest.
WantedCall = tuple[str, "TraceTarget", str]
#: At most this many functions are traced in one run.
MAX_TARGETS: Final = 2000
_DIGEST: Final = 32  # BLAKE2b-128, hexadecimal
_OUTCOMES: Final = frozenset({"return", "raise", "suspended", "unfinished"})
_PHASES: Final = frozenset({"setup", "call", "teardown"})
_PLACE_FIELDS: Final = 3


def pytest_argv(
    python: str, *, out_dir: str, tests: Sequence[str] = (), extra: Sequence[str] = ()
) -> list[str]:
    """The command: Repro's options, then the node IDs to run (all tests if none)."""
    return [
        python, "-m", "pytest", "-o", "addopts=", "-p", PLUGIN, "-p", "no:randomly",
        "-p", "no:xdist", "-p", "no:cacheprovider", "--continue-on-collection-errors",
        "--junitxml", f"{out_dir}/{JUNIT}", "-q", "-rN", *extra, *tests,
    ]  # fmt: skip


def plugin_env(
    out_dir: str,
    *,
    root: str | None = None,
    seed: int = 0,
    net_guard: bool = True,
    map_tests: bool = False,
) -> dict[str, str]:
    """What the plugin and the determinism controls need, on top of the sandbox's environment;
    ``root`` is the snapshot, which places are relative to."""
    env = {
        "REPRO_OUT": out_dir,
        "REPRO_SEED": str(seed),
        "PYTHONUTF8": "1",
        "TZ": "UTC",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }
    if net_guard:
        env["REPRO_NET_GUARD"] = "1"
    if root is not None:
        env["REPRO_ROOT"] = root
    if map_tests:
        env["REPRO_MAP"] = "1"
    return env


@dataclass(frozen=True, slots=True)
class TestResult:
    nodeid: str
    outcome: TestOutcome
    duration_s: float
    phase: str | None  # where it did not pass: setup, call or teardown
    message: str | None
    #: The call phase alone (the test's body, not its fixtures): what timing compares (D07).
    call_s: float | None = None


@dataclass(frozen=True, slots=True)
class CollectError:
    nodeid: str
    message: str


@dataclass(frozen=True, slots=True)
class PytestRun:
    tests: dict[str, TestResult]
    collect_errors: tuple[CollectError, ...]
    #: The session record: exit status, collected count, versions. None if the run never ended.
    session: dict[str, Any] | None
    malformed: int = 0
    notes: tuple[str, ...] = field(default=())
    #: Each test collected, and where its function is (None: not a Python function, or outside
    #: the snapshot).
    collected: dict[str, Place | None] = field(default_factory=dict)
    #: Each parametrized case collected whose parameters are plain values: a digest of them
    #: (the plugin's ``_case``), to tell an unchanged case from one whose parameters changed.
    cases: dict[str, str] = field(default_factory=dict)
    #: With the map on: the places each test ran, from its setup to its teardown.
    maps: dict[str, frozenset[Place]] = field(default_factory=dict)
    #: Why the map could not be made, if it could not.
    map_unavailable: str | None = None
    #: With tracing: every recorded call, and the (test, target) pairs whose budget ran out.
    calls: tuple["TraceCall", ...] = ()
    truncated: tuple[tuple[str, TraceTarget], ...] = ()
    #: Why some of the trace is missing (its file reached its limit, or was cut short).
    trace_cut: str | None = None

    @property
    def complete(self) -> bool:
        return self.session is not None

    def outcomes(self) -> dict[str, str]:
        return {nodeid: result.outcome for nodeid, result in self.tests.items()}


def _test(record: dict[str, Any]) -> TestResult:
    nodeid, outcome = record["nodeid"], record["outcome"]
    if not isinstance(nodeid, str) or not nodeid or outcome not in OUTCOMES:
        raise ValueError(record)
    phase, message, call = record.get("phase"), record.get("message"), record.get("call_s")
    if call is not None and (not isinstance(call, (int, float)) or call < 0):
        raise ValueError(record)
    return TestResult(
        nodeid=nodeid[:_TEXT],
        outcome=outcome,
        duration_s=float(record.get("duration_s", 0.0)),
        phase=phase if phase in ("setup", "call", "teardown") else None,
        message=str(message)[:_TEXT] if message is not None else None,
        call_s=float(call) if call is not None else None,
    )


def _place(raw: object) -> Place:
    if not isinstance(raw, list) or len(raw) != _PLACE_FIELDS:
        raise ValueError(raw)
    path, qualname, line = raw
    if not isinstance(path, str) or not isinstance(qualname, str) or not isinstance(line, int):
        raise ValueError(raw)
    parts = path.split("/")
    if not path or len(path) > _TEXT or path.startswith("/") or ".." in parts or ":" in path:
        raise ValueError(path)  # relative to the snapshot, inside it
    if not qualname or len(qualname) > _TEXT or line < 0:
        raise ValueError(raw)
    return path, qualname, line


def _nodeid(record: dict[str, Any]) -> str:
    nodeid = record["nodeid"]
    if not isinstance(nodeid, str) or not nodeid:
        raise ValueError(record)
    return nodeid[:_TEXT]


@dataclass(frozen=True, slots=True)
class TraceCall:
    """One call of a traced function, as the tracer recorded it (digests, never values)."""

    test: str
    phase: str
    target: TraceTarget
    index: int  # its order among the target's calls in the test, from 0
    args: str
    args_complete: bool
    #: "return", "raise", "suspended" (a generator that did not run to its end) or
    #: "unfinished" (in flight when its target's budget ran out)
    outcome: str
    result: str  # the value's digest; for "raise", the exception's arguments'
    result_complete: bool
    type: str  # the value's (or the exception's) type, module.qualname
    yields: tuple[str, ...]
    yields_complete: bool
    #: The result's digest with every dictionary sorted, when it differs from ``result``.
    result_any_order: str | None = None
    #: The arguments' digest when the call ended, where they changed in place (E04).
    args_after: str | None = None
    args_after_complete: bool = True
    #: Previews, for the calls a run asked them for (``run_tests(previews=...)``): the call,
    #: its result or exception, and its arguments afterwards if they changed.
    args_preview: str | None = None
    result_preview: str | None = None
    args_after_preview: str | None = None


def _target(raw: object) -> TraceTarget:
    if not isinstance(raw, list) or len(raw) != len(("path", "name", "start", "end")):
        raise ValueError(raw)
    path, qualname, line = _place(raw[:3])
    end = raw[3]
    if not isinstance(end, int) or end < line:
        raise ValueError(raw)
    return path, qualname, line, end


def _hex(raw: object, *, empty: bool = False) -> str:
    if not isinstance(raw, str) or not (
        (len(raw) == _DIGEST and all(c in "0123456789abcdef" for c in raw)) or (empty and raw == "")
    ):
        raise ValueError(raw)
    return raw


def _call(record: dict[str, Any]) -> TraceCall:
    outcome, phase, index = record["outcome"], record["phase"], record["index"]
    yields = record["yields"]
    if outcome not in _OUTCOMES or phase not in _PHASES or not isinstance(index, int) or index < 0:
        raise ValueError(record)
    if not isinstance(yields, list) or len(yields) > MAX_PLACES:
        raise ValueError(record)
    return TraceCall(
        test=_nodeid(record),
        phase=phase,
        target=_target(record["target"]),
        index=index,
        args=_hex(record["args"]),
        args_complete=record["args_complete"] is True,
        outcome=outcome,
        result=_hex(record["result"], empty=outcome in ("suspended", "unfinished")),
        result_complete=record["result_complete"] is True,
        type=str(record["type"])[:_TEXT],
        yields=tuple(_hex(item) for item in yields),
        yields_complete=record["yields_complete"] is True,
        result_any_order=_optional_hex(record.get("result_any_order")),
        args_after=_optional_hex(record.get("args_after")),
        args_after_complete=record.get("args_after_complete", True) is True,
        args_preview=_preview(record.get("args_preview")),
        result_preview=_preview(record.get("result_preview")),
        args_after_preview=_preview(record.get("args_after_preview")),
    )


def _preview(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(raw)
    return raw[:PREVIEW]


def _optional_hex(raw: object) -> str | None:
    return None if raw is None else _hex(raw)


def _functions(raw: object) -> frozenset[Place]:
    if not isinstance(raw, list) or len(raw) > MAX_PLACES:
        raise ValueError("functions")
    return frozenset(_place(item) for item in raw)


def _session(record: dict[str, Any]) -> dict[str, Any]:
    exitstatus, collected = record["exitstatus"], record["collected"]
    if not isinstance(exitstatus, int) or not isinstance(collected, int):
        raise ValueError(record)
    return {
        "exitstatus": exitstatus,
        "collected": collected,
        "pytest": str(record.get("pytest"))[:50],
        "python": str(record.get("python"))[:50],
        "seed": record.get("seed"),
    }


class _Reading:
    """What a results file holds so far, one record kind at a time."""

    def __init__(self) -> None:
        self.tests: dict[str, TestResult] = {}
        self.errors: list[CollectError] = []
        self.session: dict[str, Any] | None = None
        self.collected: dict[str, Place | None] = {}
        self.cases: dict[str, str] = {}
        self.maps: dict[str, frozenset[Place]] = {}
        self.unavailable: str | None = None
        self.calls: list[TraceCall] = []
        self.truncated: list[tuple[str, TraceTarget]] = []
        self.cut: int | None = None  # the trace's bytes written before its file was closed

    def test(self, record: dict[str, Any]) -> None:
        result = _test(record)
        self.tests[result.nodeid] = result

    def collect_error(self, record: dict[str, Any]) -> None:
        nodeid, message = str(record["nodeid"]), str(record["message"])
        self.errors.append(CollectError(nodeid[:_TEXT], message[:_TEXT]))

    def session_record(self, record: dict[str, Any]) -> None:
        self.session = _session(record)

    def collected_record(self, record: dict[str, Any]) -> None:
        test, case = record["test"], record.get("case")
        place = _place(test) if test is not None else None
        nodeid = _nodeid(record)
        if case is not None:
            self.cases[nodeid] = _hex(case)
        self.collected[nodeid] = place

    def map_record(self, record: dict[str, Any]) -> None:
        self.maps[_nodeid(record)] = _functions(record["functions"])

    def map_unavailable(self, record: dict[str, Any]) -> None:
        self.unavailable = str(record["reason"])[:_TEXT]

    def call(self, record: dict[str, Any]) -> None:
        self.calls.append(_call(record))

    def truncated_record(self, record: dict[str, Any]) -> None:
        self.truncated.append((_nodeid(record), _target(record["target"])))

    def trace_cut(self, record: dict[str, Any]) -> None:
        written = record["bytes"]
        if not isinstance(written, int) or written < 0:
            raise ValueError(record)
        self.cut = written

    def add(self, record: dict[str, Any]) -> None:
        reader = {
            "test": self.test,
            "collect_error": self.collect_error,
            "session": self.session_record,
            "collected": self.collected_record,
            "map": self.map_record,
            "map_unavailable": self.map_unavailable,
            "trace_cut": self.trace_cut,
        }.get(record["kind"])
        if reader is None:
            raise ValueError(record["kind"])
        reader(record)

    def add_traced(self, record: dict[str, Any]) -> None:
        """A record of the trace's own file."""
        reader = {"call": self.call, "truncated": self.truncated_record}.get(record["kind"])
        if reader is None:
            raise ValueError(record["kind"])
        reader(record)


def read_results(
    data: bytes | None, calls: bytes | None = None, *, calls_cut: bool = False
) -> PytestRun:
    """What the plugin wrote to ``results.jsonl`` (None: it wrote nothing at all) and, when
    tracing, to ``calls.jsonl`` (``calls_cut``: that file was longer than was read)."""
    if data is None:
        return PytestRun({}, (), None, notes=("the plugin wrote no results",))
    reading = _Reading()
    malformed = 0
    notes: list[str] = []
    for count, line in enumerate(data.splitlines()):
        if count >= MAX_RECORDS:
            notes.append(f"more than {MAX_RECORDS} records: the rest were not read")
            break
        try:
            reading.add(json.loads(line))
        except (ValueError, KeyError, TypeError, AttributeError):
            malformed += 1
    if reading.session is None:
        notes.append("the run did not finish: no session record")
    cut = None
    if reading.cut is not None:
        cut = f"the trace reached its limit of {MAX_BYTES // (1024 * 1024)} MiB"
    elif calls_cut:
        cut = f"the trace was longer than the {MAX_BYTES // (1024 * 1024)} MiB read"
    for count, line in enumerate((calls or b"").splitlines()):
        if count >= MAX_CALLS:
            cut = f"more than {MAX_CALLS} calls were traced: the rest were not read"
            break
        try:
            reading.add_traced(json.loads(line))
        except (ValueError, KeyError, TypeError, AttributeError):
            malformed += 1
    return PytestRun(
        reading.tests,
        tuple(reading.errors),
        reading.session,
        malformed,
        tuple(notes),
        collected=reading.collected,
        cases=reading.cases,
        maps=reading.maps,
        map_unavailable=reading.unavailable,
        calls=tuple(reading.calls),
        truncated=tuple(reading.truncated),
        trace_cut=cut,
    )


@dataclass(frozen=True, slots=True)
class TestRun:
    """One run of a side's tests: how the command ran, and what the plugin reported."""

    execution: Execution
    results: PytestRun
    junit: bytes | None


def _tracing(
    trace: Sequence[TraceTarget], previews: Sequence[WantedCall], samples: int
) -> tuple[dict[str, bytes], dict[str, str], dict[str, str]]:
    """What a traced run needs: files to write beside the output, the variables naming them
    (``{variable: file}``), and plain variables."""
    if len(trace) > MAX_TARGETS:
        raise ValueError(f"{len(trace)} functions to trace: at most {MAX_TARGETS}")
    if len(previews) > MAX_PREVIEWS:
        raise ValueError(f"{len(previews)} calls to preview: at most {MAX_PREVIEWS}")
    if not trace:
        return {}, {}, {}
    files = {TRACE_TARGETS: json.dumps([list(target) for target in trace]).encode()}
    named = {"REPRO_TRACE_TARGETS": TRACE_TARGETS}
    if previews:
        wanted = [[test, list(target), args] for test, target, args in previews]
        files[PREVIEW_CALLS] = json.dumps(wanted).encode()
        named["REPRO_PREVIEW_CALLS"] = PREVIEW_CALLS
    plain = {"REPRO_TRACE_SAMPLES": str(samples)} if samples else {}
    return files, named, plain


def _selection(tests: Sequence[str]) -> tuple[list[str], dict[str, bytes], dict[str, str]]:
    """``tests`` as pytest's arguments, or, when their IDs are too long for a command line,
    their files (or none, the project's own test paths, when those are too long as well) with
    the IDs in ``select.json``: the files to write and the variable naming them."""
    if sum(len(test) + 1 for test in tests) <= ARGV_IDS_MAX:
        return list(tests), {}, {}
    files = list(dict.fromkeys(test.partition("::")[0] for test in tests))
    if sum(len(file) + 1 for file in files) > ARGV_IDS_MAX:
        files = []
    return files, {SELECT: json.dumps(list(tests)).encode()}, {"REPRO_SELECT": SELECT}


def run_tests(
    sandbox: ProcessSandbox | RemoteSandbox,
    env: Environment,
    *,
    tests: Sequence[str] = (),
    seed: int = 0,
    hash_seed: int | None = None,
    map_tests: bool = False,
    collect_only: bool = False,
    directory: str | None = None,
    trace: Sequence[TraceTarget] = (),
    previews: Sequence[WantedCall] = (),
    samples: int = 0,
    timeout_s: float | None = None,
    stop: Callable[[], bool] | None = None,
) -> TestRun:
    """Runs ``tests`` (node IDs, in that order; all if empty) with ``env``'s interpreter in
    ``sandbox``; with ``map_tests``, records the functions each runs; with ``collect_only``,
    only lists them. ``hash_seed`` replaces the sandbox's ``PYTHONHASHSEED``; ``directory``
    (relative to the snapshot, POSIX) runs from there instead of the snapshot's root. With
    ``trace``, its functions are traced (E01), ``previews`` names calls to preview (E04) and
    ``samples`` how many of each test's first calls of each function to preview (E07)."""
    name = f"pytest-{uuid.uuid4().hex[:12]}"
    # The project's own test selection (its addopts' -m, -k, ignores...), then Repro's (H02).
    extra = [*selection(env.project.root), *(["--collect-only"] if collect_only else [])]
    files, named, plain = _tracing(trace, previews, samples)
    tests, chosen, chosen_named = _selection(tests)
    files, named = files | chosen, named | chosen_named
    if isinstance(sandbox, RemoteSandbox):
        relative = f".repro/{name}"
        out = f"{WORK}/{relative}"
        root = sandbox.container_path(directory or ".")
        variables = plugin_env(out, root=root, seed=seed, map_tests=map_tests)
        if files:
            sandbox.write_files(relative, files)
        variables |= {variable: f"{out}/{file}" for variable, file in named.items()} | plain
        if hash_seed is not None:
            variables["PYTHONHASHSEED"] = str(hash_seed)
        execution = sandbox.run(
            pytest_argv(env.python, out_dir=out, tests=tests, extra=extra),
            cwd=directory or ".",
            env=variables,
            timeout_s=timeout_s,
            stop=stop,
        )
        results = sandbox.read_file(f"{relative}/{RESULTS}", limit=MAX_BYTES)
        junit = sandbox.read_file(f"{relative}/{JUNIT}", limit=MAX_BYTES)
        calls = sandbox.read_file(f"{relative}/{CALLS}", limit=MAX_BYTES + 1) if trace else None
        calls_cut = calls is not None and len(calls) > MAX_BYTES
        calls = calls[:MAX_BYTES] if calls is not None else None
    else:
        output = sandbox.work / name
        here = env.project.root / directory if directory else env.project.root
        variables = plugin_env(str(output), root=str(here), seed=seed, map_tests=map_tests)
        if files:
            output.mkdir(parents=True)
            for file, data in files.items():
                (output / file).write_bytes(data)
        variables |= {variable: str(output / file) for variable, file in named.items()} | plain
        if hash_seed is not None:
            variables["PYTHONHASHSEED"] = str(hash_seed)
        execution = sandbox.run(
            pytest_argv(env.python, out_dir=output.as_posix(), tests=tests, extra=extra),
            cwd=here,
            env=variables,
            timeout_s=timeout_s,
            stop=stop,
            writable=(output,),
        )
        results, junit = _read(output / RESULTS), _read(output / JUNIT)
        calls, calls_cut = None, False
        if trace and (output / CALLS).is_file():
            calls, calls_cut = read_head(output / CALLS, MAX_BYTES)
    return TestRun(execution, read_results(results, calls, calls_cut=calls_cut), junit)


def _read(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    return read_head(path, MAX_BYTES)[0]
