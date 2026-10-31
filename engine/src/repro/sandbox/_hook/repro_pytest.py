"""Repro's pytest plugin (D04), loaded with ``-p repro_pytest`` inside the sandbox.

The standard library and pytest only, and Python 3.12 to 3.15: it runs in the target's
environment, never in Repro's (ARCHITECTURE §3). It talks to the engine through files in
``REPRO_OUT``:

- ``results.jsonl``: one JSON object per line, written as the run goes (a crash still leaves
  what came before): ``{"kind": "collected", ...}`` per test collected, with where its function
  is; ``{"kind": "test", ...}`` per test run; ``{"kind": "collect_error", ...}`` per module that
  failed to collect; with ``REPRO_MAP=1``, ``{"kind": "map", ...}`` per test, the functions it
  ran; and ``{"kind": "session", ...}`` last.

A function is a *place*: its file relative to ``REPRO_ROOT`` (the snapshot), its code object's
qualified name and first line, which the engine turns into a symbol. Code outside the root (the
standard library, installed packages) has no place and is not recorded.

Methods that code writes for a class have no file of their own: dataclasses, attrs and
``NamedTuple`` compile them from strings (``<string>``, ``<attrs generated methods m.C>``). In
the map, such a method's place is its class's: the class in whose ``__dict__`` it is, found
through the method's first argument (the instance, or the class for ``__new__``), as the
class's file, ``<class qualname>.<name>`` and the class's first line (``__firstlineno__``,
Python 3.13 on; 0 before). So a class whose methods are all written for it is mapped when a
test constructs it. Only descriptors taken from ``type`` and ``ModuleType`` are read, so none
of the target's code runs to find the class.

The map (D05) uses ``sys.monitoring``: PY_START is recorded once per function and test, then
disabled at that place until the next test restarts events, so a function already seen costs
nothing more in that test. Repro takes tool ID 4, 3, 2 or 5, whichever is free first, and leaves
0 (debuggers) and 1 (coverage) to their owners; with none free the map (and the trace) is
reported unavailable.

With ``REPRO_TRACE_TARGETS`` (E01, a JSON file of targets), the same tool traces the change's
target functions call by call (``repro_trace``, beside this file), into a file of their own
(E03), so a large trace never crowds out the results: ``calls.jsonl`` holds
``{"kind": "call", ...}`` per call and ``{"kind": "truncated", ...}`` when a target's budget for
a test is spent. It is written once per test, and stops at ``MAX_TRACE_BYTES``: then
``{"kind": "trace_cut", ...}`` in ``results.jsonl`` says how much was written.

Determinism: before each test, ``random`` is seeded from ``REPRO_SEED`` and the test's node ID,
so a test's random draws do not depend on which tests ran before it. With ``REPRO_NET_GUARD=1``
TCP connections to anything but the loopback interface, and resolving any name but
``localhost``, fail at once, so a test that needs the network fails the same way every time and
on every isolation level, instead of hanging or flaking. It detects; it is not a boundary
(ADR-0004): UDP ``sendto`` and C extensions are not stopped, only recorded by the audit hook
beside this file.
"""
# ruff: noqa: PTH109, PTH118 - os.path on purpose: plain strings, compared once per function

import errno
import hashlib
import inspect
import ipaddress
import json
import os
import platform
import random
import socket
import sys
from pathlib import Path
from types import FunctionType, ModuleType
from typing import Any

import pytest

_OUT = os.environ.get("REPRO_OUT")
_SEED = int(os.environ.get("REPRO_SEED", "0") or 0)
_LONGREPR = 2000
#: sys.monitoring tool IDs tried in turn: 0 (debuggers) and 1 (coverage) are left alone.
_TOOL_IDS = (4, 3, 2, 5)
#: The trace file's size at most (the engine reads no more than this).
MAX_TRACE_BYTES = 64 * 1024 * 1024
_STATE: dict[str, Any] = {
    "file": None, "calls": None, "traced": 0, "tests": {}, "places": None, "monitor": None,
}  # fmt: skip

Place = tuple[str, str, int]
# Read through ``type``'s and ``ModuleType``'s own descriptors: a metaclass's or a module's
# ``__getattribute__``, ``__getattr__`` or properties never run.
_MRO = type.__dict__["__mro__"].__get__
_TYPE_DICT = type.__dict__["__dict__"].__get__
_QUALNAME = type.__dict__["__qualname__"].__get__
_MODULE_DICT = vars(ModuleType)["__dict__"].__get__


def _write(record: dict[str, Any]) -> None:
    handle = _STATE["file"]
    if handle is not None:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
    calls = _STATE["calls"]
    if calls is not None:
        # The trace goes out with the results' records (a test's own ends it): a flush per
        # traced call cost about 2.3 us on Windows (E02). A process killed mid-test loses at
        # most that test's calls, and a cut last line is counted as malformed.
        calls.flush()


def _trace(record: dict[str, Any]) -> None:
    """A tracer record, to the trace file, until it holds ``MAX_TRACE_BYTES``."""
    handle = _STATE["calls"]
    if handle is None:
        return
    line = json.dumps(record, sort_keys=True) + "\n"
    written = _STATE["traced"] + len(line)
    if written > MAX_TRACE_BYTES:
        handle.close()
        _STATE["calls"] = None
        _write({"kind": "trace_cut", "bytes": _STATE["traced"], "limit": MAX_TRACE_BYTES})
        return
    handle.write(line)
    _STATE["traced"] = written


def _seed_for(nodeid: str) -> int:
    digest = hashlib.sha256(f"{_SEED}:{nodeid}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _host(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")
    return value if isinstance(value, str) else None


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


def _guard_network() -> None:
    """Connections to anything but loopback fail at once, and so does resolving any name but
    ``localhost``; each refusal is raised as the audit event it stands for, so the hook records
    it. Detection evidence and a fixed failure, not a boundary (L3 has no network at all)."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def check(sock: socket.socket, address: Any) -> None:
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return
        host = _host(address[0]) if isinstance(address, tuple) and address else None
        ip = _ip(host) if host is not None else None
        if host == "localhost" or (ip is not None and ip.is_loopback):
            return
        sys.audit("socket.connect", sock, address)
        raise OSError(errno.ENETUNREACH, f"network access is off in Repro's sandbox: {address!r}")

    def connect(self: socket.socket, address: Any) -> None:
        check(self, address)
        return original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        check(self, address)
        return original_connect_ex(self, address)

    def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        name = _host(host)
        if name is not None and name != "localhost" and _ip(name) is None:
            sys.audit("socket.getaddrinfo", host, port, 0, 0, 0)
            raise socket.gaierror(
                socket.EAI_NONAME, f"name resolution is off in Repro's sandbox: {name!r}"
            )
        return original_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign,assignment]
    socket.getaddrinfo = getaddrinfo


def _code_of(value: Any) -> Any:
    """The code of a function as a class's ``__dict__`` holds it (plain, static or class
    method), or None. Exact types only: no descriptor of the target's is called."""
    kind = type(value)
    if kind is staticmethod or kind is classmethod:
        value = value.__func__
        kind = type(value)
    return value.__code__ if kind is FunctionType else None


class _Places:
    """Each code object's place, or None outside the root; computed once per code object (keyed
    by identity and held, since equal code objects in two files compare equal)."""

    def __init__(self, root: str) -> None:
        self.root = os.path.join(os.path.realpath(root), "")
        self.folded = os.path.normcase(self.root)
        self.known: dict[int, tuple[Any, Place | None]] = {}
        self.written: dict[int, tuple[Any, Place | None]] = {}  # code with no file: its class's

    def relative(self, filename: Any) -> str | None:
        """A file's path relative to the root, or None for no file or one outside the root."""
        if type(filename) is not str or not filename or filename.startswith("<"):
            return None
        real = os.path.realpath(filename)
        if not os.path.normcase(real).startswith(self.folded):
            return None
        return real[len(self.root) :].replace(os.sep, "/")

    def of(self, code: Any) -> Place | None:
        known = self.known.get(id(code))
        if known is not None:
            return known[1]
        relative = self.relative(code.co_filename)
        place = None if relative is None else (relative, code.co_qualname, code.co_firstlineno)
        self.known[id(code)] = (code, place)
        return place

    def written_for(self, code: Any, frame: Any) -> Place | None:
        """The place of a method code wrote for a class (the module's docstring), or None;
        decided once, at its first call in a test, from the frame's first argument."""
        known = self.written.get(id(code))
        if known is not None:
            return known[1]
        place = None
        if code.co_argcount and not code.co_filename.startswith("<frozen "):  # not the stdlib's
            place = self._owner(code, frame.f_locals.get(code.co_varnames[0]))
        self.written[id(code)] = (code, place)
        return place

    def _owner(self, code: Any, first: Any) -> Place | None:
        """The place of the class whose ``__dict__`` holds ``code``: along the MRO of ``first``
        when it is a class (``__new__`` receives one), then of its type."""
        kind = type(first)
        for start in (first, kind) if issubclass(kind, type) else (kind,):
            mro = _MRO(start)
            for klass in mro if type(mro) is tuple else ():
                for name, value in _TYPE_DICT(klass).items():
                    if _code_of(value) is code:
                        return self._class_place(klass, name)
        return None

    def _class_place(self, klass: type, name: str) -> Place | None:
        own = _TYPE_DICT(klass)
        module_name, line = own.get("__module__"), own.get("__firstlineno__", 0)
        module = sys.modules.get(module_name) if type(module_name) is str else None
        if module is None or not issubclass(type(module), ModuleType):
            return None
        relative = self.relative(_MODULE_DICT(module).get("__file__"))
        qualname = _QUALNAME(klass)
        if relative is None or type(qualname) is not str:
            return None
        return (relative, f"{qualname}.{name}", line if type(line) is int else 0)


class _Monitor:
    """Repro's ``sys.monitoring`` tool: the places each test runs (the map: PY_START once per
    function and test, then DISABLE), and, with a tracer, every call of the target functions."""

    def __init__(self, places: _Places, tool: int, mapping: bool, tracer: Any) -> None:
        self.places = places
        self.tool = tool
        self.mapping = mapping
        self.tracer = tracer
        self.current: set[Place] | None = None
        self.local: set[int] = set()  # targets whose local events are set
        self.events = sys.monitoring.events

    def started(self, code: Any, _offset: int) -> Any:
        place = self.places.of(code)
        if place is None:
            current = self.current
            if current is not None and code.co_filename.startswith("<"):
                written = self.places.written_for(code, sys._getframe(1))
                if written is not None:  # mapped as its class; never a tracer's target
                    current.add(written)
            return sys.monitoring.DISABLE
        current = self.current
        if current is not None and self.mapping:
            current.add(place)
        tracer = self.tracer
        if tracer is not None:
            target = tracer.target(code, place)
            if target is not None:
                if id(code) not in self.local:
                    self.local.add(id(code))
                    local = self.events.PY_RETURN | self.events.PY_YIELD
                    sys.monitoring.set_local_events(self.tool, code, local)
                if tracer.start(target, code, sys._getframe(1)):
                    return None
        return sys.monitoring.DISABLE

    def returned(self, code: Any, _offset: int, value: Any) -> Any:
        if self.tracer.returned(sys._getframe(1), value) or not self.tracer.idle(code):
            return None
        return self._retire(code)

    def yielded(self, code: Any, _offset: int, value: Any) -> Any:
        if self.tracer.yielded(sys._getframe(1), value) or not self.tracer.idle(code):
            return None
        return self._retire(code)

    def _retire(self, code: Any) -> Any:
        """A target past its budget with nothing in flight: its local events are removed (not
        only disabled where they fired) until the next test sets them again."""
        sys.monitoring.set_local_events(self.tool, code, 0)
        self.local.discard(id(code))
        return sys.monitoring.DISABLE

    def unwound(self, code: Any, _offset: int, exc: BaseException) -> Any:
        if id(code) in self.local:
            self.tracer.unwound(sys._getframe(1), exc)
            return None
        # Global before 3.15, where DISABLE here is an error; per code object from 3.15 on.
        return sys.monitoring.DISABLE if sys.version_info >= (3, 15) else None

    def begin(self, test: str) -> None:
        self.current = set() if self.mapping else None
        sys.monitoring.restart_events()
        if self.tracer is not None:
            self.tracer.begin(test)

    def end(self) -> list[list[Any]]:
        if self.tracer is not None:
            self.tracer.end()
        found, self.current = self.current or set(), None
        return [list(place) for place in sorted(found)]

    def stop(self) -> None:
        monitoring = sys.monitoring
        monitoring.set_events(self.tool, 0)
        for event in (self.events.PY_START, self.events.PY_RETURN, self.events.PY_YIELD,
                      self.events.PY_UNWIND):  # fmt: skip
            monitoring.register_callback(self.tool, event, None)
        monitoring.free_tool_id(self.tool)


def _start_monitor(places: _Places, *, mapping: bool, tracer: Any) -> _Monitor | None:
    monitoring = sys.monitoring
    events = monitoring.events
    for tool in _TOOL_IDS:
        try:
            monitoring.use_tool_id(tool, "repro")
        except ValueError:
            continue
        monitor = _Monitor(places, tool, mapping, tracer)
        monitoring.register_callback(tool, events.PY_START, monitor.started)
        wanted = events.PY_START
        if tracer is not None:
            monitoring.register_callback(tool, events.PY_RETURN, monitor.returned)
            monitoring.register_callback(tool, events.PY_YIELD, monitor.yielded)
            monitoring.register_callback(tool, events.PY_UNWIND, monitor.unwound)
            wanted |= events.PY_UNWIND
        monitoring.set_events(tool, wanted)
        return monitor
    return None


def pytest_configure() -> None:
    if _OUT:
        Path(_OUT).mkdir(parents=True, exist_ok=True)
        _STATE["file"] = (Path(_OUT) / "results.jsonl").open("w", encoding="utf-8")
    if os.environ.get("REPRO_NET_GUARD") == "1":
        _guard_network()
    places = _Places(os.environ.get("REPRO_ROOT") or os.getcwd())
    _STATE["places"] = places
    mapping = os.environ.get("REPRO_MAP") == "1"
    targets = os.environ.get("REPRO_TRACE_TARGETS")
    tracer = None
    if targets:
        import repro_trace  # noqa: PLC0415 - only when tracing

        listed = Path(targets).read_text(encoding="utf-8")
        if _OUT:
            _STATE["calls"] = (Path(_OUT) / "calls.jsonl").open("w", encoding="utf-8")
        wanted = os.environ.get("REPRO_PREVIEW_CALLS")
        previews = None
        if wanted:  # E04: the calls whose values the engine asks to see
            previews = repro_trace.parse_previews(Path(wanted).read_text(encoding="utf-8"))
        samples = int(os.environ.get("REPRO_TRACE_SAMPLES", "0") or 0)  # E07
        tracer = repro_trace.Tracer(
            repro_trace.parse_targets(listed), _trace, previews, samples=max(0, samples)
        )
    if mapping or tracer is not None:
        monitor = _start_monitor(places, mapping=mapping, tracer=tracer)
        _STATE["monitor"] = monitor
        if monitor is None:
            _write({"kind": "map_unavailable", "reason": "no sys.monitoring tool ID is free"})


def pytest_unconfigure() -> None:
    monitor = _STATE["monitor"]
    if monitor is not None:
        monitor.stop()
        _STATE["monitor"] = None


def _test_place(item: Any) -> Place | None:
    """Where a test's own function is (None for tests that are not Python functions)."""
    function = getattr(item, "function", None)
    try:
        function = inspect.unwrap(function) if function is not None else None
    except ValueError:  # a cycle of __wrapped__
        return None
    code = getattr(function, "__code__", None)
    places: _Places | None = _STATE["places"]
    return places.of(code) if code is not None and places is not None else None


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """With ``REPRO_SELECT``: only the tests that file lists, in its order (J04). Repro passes
    tests this way when their IDs are too long for a command line; last, so that what runs is
    exactly the list, whatever the project's own plugins did."""
    chosen = os.environ.get("REPRO_SELECT")
    if not chosen:
        return
    with open(chosen, encoding="utf-8") as listed:  # noqa: PTH123 - a path from Repro
        order = {nodeid: index for index, nodeid in enumerate(json.load(listed))}
    kept = sorted((item for item in items if item.nodeid in order), key=lambda i: order[i.nodeid])
    dropped = [item for item in items if item.nodeid not in order]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept


def pytest_collection_finish(session: Any) -> None:
    for item in session.items:
        place = _test_place(item)
        _write({"kind": "collected", "nodeid": item.nodeid, "test": list(place) if place else None})


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: Any) -> Any:
    """With the map on: the places a test runs, from its setup to its teardown; with the
    tracer, the calls of its targets."""
    monitor: _Monitor | None = _STATE["monitor"]
    if monitor is None:
        yield
        return
    monitor.begin(item.nodeid)
    try:
        yield
    finally:
        functions = monitor.end()
        if monitor.mapping:
            _write({"kind": "map", "nodeid": item.nodeid, "functions": functions})


def _phase(name: str) -> None:
    monitor: _Monitor | None = _STATE["monitor"]
    if monitor is not None and monitor.tracer is not None:
        monitor.tracer.phase = name


@pytest.hookimpl(tryfirst=True)  # before the fixtures, so their draws are seeded too
def pytest_runtest_setup(item: Any) -> None:
    random.seed(_seed_for(item.nodeid))
    _phase("setup")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call() -> None:
    _phase("call")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown() -> None:
    _phase("teardown")


def _outcome(report: Any) -> str:
    """One phase's outcome: passed, failed, skipped, xfailed, xpassed, or error (setup or
    teardown failed)."""
    if hasattr(report, "wasxfail"):
        return "xpassed" if report.passed else "xfailed"
    if report.failed:
        return "failed" if report.when == "call" else "error"
    return "skipped" if report.skipped else "passed"


def _message(report: Any) -> str | None:
    return str(report.longrepr)[-_LONGREPR:] if report.longrepr else None


def pytest_runtest_logreport(report: Any) -> None:
    """A test's outcome from its three phases, written once its teardown has run: a setup that
    does not pass decides it (error, skip, xfail); otherwise the call does; a teardown that
    fails turns a pass into an error."""
    tests: dict[str, dict[str, Any]] = _STATE["tests"]
    entry = tests.setdefault(
        report.nodeid,
        {"kind": "test", "nodeid": report.nodeid, "outcome": None, "duration_s": 0.0,
         "call_s": None, "phase": None, "message": None},
    )  # fmt: skip
    entry["duration_s"] += float(getattr(report, "duration", 0.0))
    outcome = _outcome(report)
    if report.when == "setup" and outcome != "passed":
        entry.update(outcome=outcome, phase="setup", message=_message(report))
    elif report.when == "call":
        entry["outcome"] = outcome
        entry["call_s"] = round(float(getattr(report, "duration", 0.0)), 9)  # D07: timing
        if outcome != "passed":
            entry.update(phase="call", message=_message(report))
    elif report.when == "teardown":
        if entry["outcome"] is None:
            entry["outcome"] = "passed"
        if outcome == "error" and entry["outcome"] == "passed":
            entry.update(outcome="error", phase="teardown", message=_message(report))
        entry["duration_s"] = round(entry["duration_s"], 6)
        _write(tests.pop(report.nodeid))  # a rerun of the same test starts afresh


def pytest_collectreport(report: Any) -> None:
    if report.failed:
        _write(
            {
                "kind": "collect_error",
                "nodeid": report.nodeid,
                "message": str(report.longrepr)[-_LONGREPR:],
            }
        )


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    _write(
        {
            "kind": "session",
            "exitstatus": int(exitstatus),
            "collected": int(getattr(session, "testscollected", 0)),
            "pytest": pytest.__version__,
            "python": platform.python_version(),
            "seed": _SEED,
        }
    )
    for name in ("calls", "file"):
        handle = _STATE[name]
        if handle is not None:
            handle.close()
            _STATE[name] = None
