"""Two-sided execution (D06, ARCHITECTURE §4): the selected tests on base and head, run so that
what differs is the change and not the run.

1. **A**: base, mapped, every test (or, with the base map cached, only the selected ones).
2. Head's tests are listed, and the tests to run are selected (D05).
3. **A2**: base again, the selected tests in the reverse order, with another ``PYTHONHASHSEED``
   and another seed for ``random``.
4. **H**: head, the selected tests, mapped (coverage of head-only code, and DeFlaker's check).

With a trace plan (E03), A and A2 trace base's changed functions and H head's: the
observations ``repro.runner.observations`` compares.
5. Each flip (A and A2 agreeing, H differing) runs again *k* times on both sides.
6. The base version of each edited test runs against head's code.

A run that does not finish leaves its tests unclassified and becomes a missing item (``timeout``
or ``environment_failed``), with every collection error (``collection_error``); outcomes are only
compared where both runs reported them.
"""

from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Final

from repro.analysis.changes import SymbolChange
from repro.analysis.imports import ImportGraph
from repro.readable import error_line
from repro.runner.outcomes import RERUNS, OutcomeDiff, classify, is_flip, passes
from repro.runner.pytest import CollectError, TestRun
from repro.runner.selection import (
    MapCache,
    Resolver,
    Selected,
    TestMap,
    base_id,
    head_id,
    select,
    test_map,
    uncovered,
)
from repro.runner.sides import Side
from repro.runner.testdiff import TestChange, kept_cases, test_changes
from repro.runner.trace import TracePlan
from repro.sandbox.detect import Detection

#: A2's hash seed and ``random`` seed: anything but the sandbox's and the plugin's 0.
OTHER_HASH_SEED: Final = 1
OTHER_SEED: Final = 1
#: The traced runs, by label.
TRACED_RUNS: Final = ("A", "A2", "H")
#: Calls previewed per test and function in each traced run: the cockpit's samples (E07).
SAMPLES: Final = 4
OVERLAY: Final = "base_tests"


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One pytest run on one side: what it was for, the tests asked for, how it went."""

    side: str
    label: str  # "A", "A2", "H", "rerun 1".., "head tests", "base tests on head"
    tests: tuple[str, ...]  # the tests asked for (empty: every test)
    run: TestRun

    @property
    def maps_every_test(self) -> bool:
        """A over every test, to make base's map (not made when the map is cached)."""
        return self.label == "A" and not self.tests


def _empty() -> TestMap:
    return TestMap({}, {})


@dataclass
class TwoSided:
    selected: tuple[Selected, ...] = ()
    gaps: tuple[SymbolChange, ...] = ()
    base_map: TestMap = field(default_factory=_empty)
    head_tests: TestMap = field(default_factory=_empty)
    head_map: TestMap = field(default_factory=_empty)
    outcomes: tuple[OutcomeDiff, ...] = ()
    test_changes: tuple[TestChange, ...] = ()
    runs: list[RunRecord] = field(default_factory=list)
    map_cached: bool = False
    #: What A, A2 and H traced (None: nothing).
    trace: TracePlan | None = None
    #: The cases of edited parametrized tests that the change left as they were
    #: (``testdiff.kept_cases``): selected as ``modified_test``, compared as any other test.
    kept_cases: frozenset[str] = frozenset()

    def modified(self) -> frozenset[str]:
        """The selected tests whose own code or parameters the change edited: their base and
        head outcomes are of different tests."""
        edited = {s.test for s in self.selected if "modified_test" in s.reasons}
        return frozenset(edited - self.kept_cases)

    def traced(self) -> dict[str, RunRecord]:
        """A, A2 and H, those that ran, by label."""
        return {r.label: r for r in self.runs if r.label in TRACED_RUNS}

    def detections(self, side: str) -> list[Detection]:
        """What ``side``'s runs detected (ADR-0004). Of A, only what the selected tests attempted
        (H02): made with the map, A runs every base test, from the cached map only the selected
        ones, and what else either run does is not about the change. A2 runs the same tests
        again, so what their session does outside any test (its imports, pytest's own files) is
        still reported, once a run either way."""
        chosen = {s.test for s in self.selected}
        return [
            detection
            for record in self.runs
            if record.side == side
            for detection in record.run.execution.detections.items
            if record.label != "A" or detection.test in chosen
        ]

    def problems(self, memory_bytes: int | None = None) -> list[dict[str, Any]]:
        """``missing.recorded`` payloads for runs that did not finish or reached their memory
        limit (``memory_bytes``), collection errors, and a map that could not be made; each
        reason in words a user can act on (H01)."""
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def uncollected(side: str, errors: Iterable[CollectError]) -> None:
            for error in errors:
                if (side, error.nodeid) not in seen:
                    seen.add((side, error.nodeid))
                    detail = (
                        f"{side}: {error.nodeid} could not be collected: "
                        f"{error_line(error.message)}"
                    )
                    items.append(
                        _missing("collection_error", detail, _file(error.nodeid), COLLECT_FIX)
                    )

        # The map's first, whether made in this run or cached (H02): the same items either way.
        uncollected("base", (CollectError(*item) for item in self.base_map.uncollected.items()))
        for record in self.runs:
            results = record.run.results
            uncollected(record.side, results.collect_errors)
            memory = _memory(record, memory_bytes)
            if memory is not None:
                items.append(_missing("memory_limit", memory, resolution=MEMORY_FIX))
            if not results.complete:
                items.append(_incomplete(record))
            if results.map_unavailable:
                detail = f"{record.side}: no test map ({results.map_unavailable})"
                items.append(_missing("trace_unavailable", detail))
        return items


COLLECT_FIX: Final = "Make the module importable on this side: the error names what fails"
MEMORY_FIX: Final = (
    "If the change is meant to use more memory, raise the limit (the memory_limit_mb setting); "
    "otherwise the change's own memory use is the cause"
)
ENDED_FIX: Final = "The run's output says why: run the side's tests on their own to see it"
TIMEOUT_FIX: Final = (
    "A test that hangs or runs long on this side: find it from the tests reported, or raise "
    "the limit (the test_timeout_s setting)"
)
#: A run is said to have reached its limit at this share of it (Windows: the peak committed).
NEAR_LIMIT: Final = 0.95


def _file(nodeid: str) -> str:
    return nodeid.split("::", 1)[0] or "."


def _missing(
    reason: str, detail: str, file: str | None = None, resolution: str | None = None
) -> dict[str, Any]:
    return {
        "missing": {
            "reason": reason,
            "targets": [{"file": file}] if file else [],
            "detail": detail[:1000],
            "resolution": resolution,
        }
    }


def _size(count: int) -> str:
    return f"{count / 2**30:g} GiB" if count >= 2**30 else f"{count / 2**20:.0f} MiB"


def _memory(record: RunRecord, limit: int | None) -> str | None:
    """Why the run looks to have reached its memory limit, or None: the kernel killed it
    (Docker), its peak came within reach of the limit (Windows), or tests failed with
    ``MemoryError`` (any platform: the limit refuses an allocation)."""
    execution = record.run.execution
    starved = [
        t
        for t in record.run.results.tests.values()
        if t.outcome != "passed" and "MemoryError" in (t.message or "")
    ]
    peak = execution.peak_memory_bytes
    near = limit is not None and peak is not None and peak >= NEAR_LIMIT * limit
    if not (execution.oom_killed or near or starved):
        return None
    limit_text = f" of {_size(limit)}" if limit else ""
    why = [f"{record.side}: the {record.label} run reached its memory limit{limit_text}"]
    if execution.oom_killed:
        why.append("the system stopped it")
    if starved:
        why.append(
            f"{len(starved)} {'test' if len(starved) == 1 else 'tests'} failed with MemoryError"
        )
    return "; ".join(why)


def _incomplete(record: RunRecord) -> dict[str, Any]:
    """A run that did not finish: why, in a sentence, and how many tests it reported."""
    results, execution = record.run.results, record.run.execution
    count = len(results.tests)
    reported = f"{count} {'test' if count == 1 else 'tests'} reported before it stopped"
    if execution.outcome == "timeout":
        detail = (
            f"{record.side}: the {record.label} run took longer than its limit "
            f"({execution.duration_s:.0f} s); {reported}"
        )
        return _missing("run_incomplete", detail, resolution=TIMEOUT_FIX)
    status = execution.returncode
    ended = f"with exit status {status}" if status is not None else "before it finished"
    detail = f"{record.side}: the {record.label} run ended early, {ended}; {reported}"
    said = error_line(execution.stderr[-8192:].decode("utf-8", "replace"))
    return _missing("run_incomplete", f"{detail}: {said}" if said else detail, resolution=ENDED_FIX)


def _outcome(done: TestRun | None, test: str) -> str | None:
    found = done.results.tests.get(test) if done is not None else None
    return found.outcome if found is not None else None


def _message(done: TestRun | None, test: str) -> str | None:
    found = done.results.tests.get(test) if done is not None else None
    return found.message if found is not None else None


Observe = Callable[[RunRecord], None]
#: A run to make: its side, label, tests and options.
Planned = tuple[Side, str, Sequence[str], dict[str, Any]]


class _Reproduction:
    def __init__(
        self,
        sides: tuple[Side, Side],
        resolvers: tuple[Resolver, Resolver],
        observe: Observe | None,
        stop: Callable[[], bool] | None,
        trace: TracePlan | None = None,
    ) -> None:
        self.base, self.head = sides
        self.resolvers = resolvers
        self.observe = observe
        self.stop = stop
        self.result = TwoSided(trace=trace or None)
        self.on_base = list(trace.base) if trace else []
        self.on_head = list(trace.head) if trace else []
        self.a: TestRun | None = None
        self.a2: TestRun | None = None
        self.h: TestRun | None = None

    def run(self, side: Side, label: str, tests: Sequence[str] = (), **options: Any) -> TestRun:
        done = side.run(tests, stop=self.stop, **options)
        return self._record(side, label, tests, done)

    def pair(self, first: "Planned", second: "Planned") -> tuple[TestRun, TestRun]:
        """Two runs on different sides at once (H02: base and head are separate sandboxes),
        recorded in the order given, so that what the run reports does not depend on which
        finished first."""
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="repro-side") as pool:
            futures = [
                pool.submit(side.run, tests, stop=self.stop, **options)
                for side, _, tests, options in (first, second)
            ]
            done = [future.result() for future in futures]
        return (
            self._record(first[0], first[1], first[2], done[0]),
            self._record(second[0], second[1], second[2], done[1]),
        )

    def _record(self, side: Side, label: str, tests: Sequence[str], done: TestRun) -> TestRun:
        record = RunRecord(side.name, label, tuple(tests), done)
        self.result.runs.append(record)
        # A over every test is observed once the selection is known (run_selected).
        if self.observe is not None and not record.maps_every_test:
            self.observe(record)
        return done

    def map_base(self, cache: MapCache | None, key: str | None) -> tuple[TestRun | None, TestRun]:
        """The base map (from the cache, or from A run over every test) and head's tests,
        listed: both at once when A runs."""
        listing: Planned = (self.head, "head tests", (), {"collect_only": True})
        cached = cache.get(key) if cache is not None and key is not None else None
        if cached is not None:
            self.result.base_map, self.result.map_cached = cached, True
            return None, self.run(self.head, "head tests", collect_only=True)
        options = {"map_tests": True, "trace": self.on_base, "samples": SAMPLES}
        full, listed = self.pair((self.base, "A", (), options), listing)
        self.result.base_map = test_map(full.results, self.resolvers[0])
        if cache is not None and key is not None and full.results.complete:
            cache.put(key, self.result.base_map)
        return full, listed

    def run_selected(self, full: TestRun | None, on_base: list[str], on_head: list[str]) -> None:
        if full is not None:
            self.a = full
            if self.observe is not None:
                self.observe(RunRecord("base", "A", tuple(on_base), full))
        elif on_base:
            self.a = self.run(self.base, "A", on_base, trace=self.on_base, samples=SAMPLES)
        again: Planned = (
            self.base,
            "A2",
            list(reversed(on_base)),
            {
                "hash_seed": OTHER_HASH_SEED,
                "seed": OTHER_SEED,
                "trace": self.on_base,
                "samples": SAMPLES,
            },
        )
        head: Planned = (
            self.head,
            "H",
            on_head,
            {"map_tests": True, "trace": self.on_head, "samples": SAMPLES},
        )
        if on_base and on_head:
            self.a2, self.h = self.pair(again, head)
        elif on_base:
            self.a2 = self.run(again[0], again[1], again[2], **again[3])
        elif on_head:
            self.h = self.run(head[0], head[1], head[2], **head[3])
        if self.h is not None:
            self.result.head_map = test_map(self.h.results, self.resolvers[1])

    def rerun(self, flips: list[str]) -> dict[str, tuple[list[str | None], list[str | None]]]:
        """Each flip's outcomes when run again, on base and on head."""
        again: dict[str, tuple[list[str | None], list[str | None]]] = {t: ([], []) for t in flips}
        for attempt in range(1, RERUNS + 1) if flips else ():
            label = f"rerun {attempt}"
            on_base, on_head = self.pair(
                (self.base, label, flips, {}), (self.head, label, flips, {})
            )
            for test in flips:
                again[test][0].append(_outcome(on_base, test))
                again[test][1].append(_outcome(on_head, test))
        return again

    def classify(
        self, chosen: list[str], on_base: set[str], on_head: set[str], modified: set[str],
        changes: Sequence[SymbolChange],
    ) -> tuple[OutcomeDiff, ...]:  # fmt: skip
        flips = [
            t
            for t in chosen
            if is_flip(
                _outcome(self.a, t),
                _outcome(self.a2, t),
                _outcome(self.h, t),
                modified=t in modified,
            )
        ]
        again = self.rerun(flips)
        changed = {i for c in changes for i in (base_id(c), head_id(c)) if i}
        base_ran, head_ran = self.result.base_map.ran, self.result.head_map.ran
        diffs = []
        for test in chosen:
            a, h = _outcome(self.a, test), _outcome(self.h, test)
            if (test in on_base and a is None) or (test in on_head and h is None):
                continue  # a run that did not report it: a missing item, not an outcome
            ran = (base_ran.get(test, frozenset()) | head_ran.get(test, frozenset())) & changed
            reruns = again.get(test, ([], []))
            diffs.append(
                classify(
                    test,
                    base=a if test in on_base else None,
                    base_again=_outcome(self.a2, test),
                    head=h if test in on_head else None,
                    modified=test in modified,
                    base_reruns=reruns[0],
                    head_reruns=reruns[1],
                    ran_changed_code=bool(ran),
                    base_message=_message(self.a, test),
                    head_message=_message(self.h, test),
                )
            )
        return tuple(diffs)

    def base_tests_on_head(self, edited: list[str]) -> list[TestChange]:
        """The base version of each edited test, against head's code: a failure where it passed
        on base is a regression the edit hid (ARCHITECTURE §4.2)."""
        if not edited or self.a is None:
            return []
        files = sorted({_file(test) for test in edited})
        directory = self.head.overlay(OVERLAY, self.resolvers[0].read(files))
        done = self.run(self.head, "base tests on head", edited, directory=directory)
        found = []
        for test in edited:
            before, after = _outcome(self.a, test), _outcome(done, test)
            if before is not None and after is not None and passes(before) and not passes(after):
                detail = f"base's version passes on base, {after} on head"
                found.append(TestChange(_file(test), test, "base_test_fails_on_head", detail))
        return found


def reproduce(
    sides: tuple[Side, Side],
    changes: Sequence[SymbolChange],
    graphs: tuple[ImportGraph, ImportGraph],
    resolvers: tuple[Resolver, Resolver],
    *,
    cache: MapCache | None = None,
    cache_key: str | None = None,
    observe: Observe | None = None,
    on_selected: Callable[[tuple[Selected, ...]], None] | None = None,
    stop: Callable[[], bool] | None = None,
    trace: TracePlan | None = None,
) -> TwoSided:
    """Runs the steps of the module's docstring on ``sides`` (base, head) and classifies what
    they found. ``observe`` sees each run as it ends (A once the tests are selected), and
    ``on_selected`` the selection; ``trace`` is what A, A2 and H trace."""
    steps = _Reproduction(sides, resolvers, observe, stop, trace)
    result = steps.result
    full, listed = steps.map_base(cache, cache_key)
    result.head_tests = test_map(listed.results, resolvers[1])
    result.selected = select(changes, result.base_map, result.head_tests, graphs)
    if on_selected is not None:
        on_selected(result.selected)
    chosen = [s.test for s in result.selected]
    wanted = set(chosen)  # run in collection order, so only A2 changes the order
    on_base = [t for t in result.base_map.tests if t in wanted]
    on_head = [t for t in result.head_tests.tests if t in wanted]
    steps.run_selected(full, on_base, on_head)
    result.kept_cases = kept_cases(changes, result.base_map, result.head_tests, resolvers)
    modified = set(result.modified())
    result.outcomes = steps.classify(chosen, set(on_base), set(on_head), modified, changes)
    found = test_changes(changes, result.base_map, result.head_tests, resolvers)
    found += steps.base_tests_on_head(sorted(modified & set(on_base)))
    result.test_changes = tuple(found)
    result.gaps = uncovered(changes, result.selected, result.base_map, result.head_map)
    return result
