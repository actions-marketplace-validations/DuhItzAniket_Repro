"""The run and diff stages of a JavaScript or TypeScript change (K08, K11; ADR-0017, phase 4).

What ``repro.run_stage`` does for Python, with ``repro.jsts``'s runs, emitting the same
events: the card, the cockpit, the exports and the review stage read a JavaScript run as they
read a Python one.

- ``sandbox-<side>`` (build): the sandbox and the dependencies, installed from the lockfile with
  lifecycle scripts off; ``sandbox.ready``, then ``env.provisioned`` (the package manager,
  Node.js, the lock's digest, the packages installed).
  - With ``backend="docker"`` (K11; ``auto`` when Docker answers, as for Python): a container
    sandbox (L3; L4 under gVisor, L5 under Kata) on Repro's Node.js image
    (``sandbox.image.node_image``). The install runs in a container of its own, the only one with
    the network (``jsts.install_in_container``); Node.js's version and the packages are read in
    the sandbox, with none.
  - Otherwise: the process sandbox (L2), the install on the host.

  An image of the user's (``sandbox_image``, J04) is for Python projects, and refused.
- ``tests`` (run): A (base, with the map and the traces), H (head, the same), then the tests
  both maps select (``jsmap.select``; ``test.selected``, after the tests' graph nodes and
  ``covers`` edges), A2 (base again, shuffled, traced), and each test that flipped, run again
  ``RERUNS`` times on each side. The whole suite runs, so every test whose outcome differs is
  reported too, selected or not (``test.started``, ``test.finished``); ``trace.sample`` per
  previewed call.
- ``outcomes`` (diff): ``diff.outcome`` per reported test, classified as Python's are
  (``runner.outcomes.classify``; a test whose own code changed is ``modified``); ``diff.tests``
  for a test removed, an edited test skipped on head, and an edited test whose assertions are
  fewer, weaker or wider (``jsassert``, from the TypeScript compiler's syntax tree, K11; without
  the compiler, fewer ``expect``/``assert`` calls, counted by a scanner); and base's version of
  an edited test run against head's code (``base_test_fails_on_head``, K11). Missing items:
  flaky tests, changed functions no selected test runs, test files that did not run, maps not
  made, runs that did not finish, the checks of edited tests that could not run, and, at L2,
  the network.
- ``behaviour`` (diff): A, A2 and H's calls compared (``jstrace``) as E03 and E04 compare
  Python's: ``behaviour_diff`` evidence and ``diff.behaviour``, the masked noise and what could
  not be compared as missing items, and a changed function that ran but was never traced. A
  run's first calls of each function carry previews, which stand in for E04's reproducer runs.
- ``api`` (diff): the exports of ``package.json``'s entries compared with the TypeScript compiler
  API (``analysis.jsapi``, K11): ``api_diff`` evidence and ``diff.api`` per breaking change, and a
  missing item for each entry not read. Without Node.js or the pinned compiler, the API is a
  missing item, never a pass.
- ``deps`` (diff): ``package.json``'s dependencies and the installed packages, compared
  (``dep_diff`` evidence, ``diff.deps``).
- ``invariants`` (check): F02's (``run_stage.invariants_job``). Nothing is timed:
  ``perf_budget`` is not evaluated, and the timing is a missing item. ``no_network``:
  - at L2 it is not evaluated: a Node process's network use is neither blocked nor seen in the
    process sandbox, and the network is a missing item;
  - at L3 the containers have no network, so nothing head's tests did reached it, and it
    passes on that alone. Repro's detection is a Python audit hook, which a Node process does
    not load: an attempt is neither seen nor cited, so it cannot fail on one as a Python run
    does, and its message says so (``NODE_UNDETECTED``). A test that needed the network fails,
    and its outcome says that.
"""

import platform
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Final

from repro import __version__, jsassert, jsmap, jstrace, jsts
from repro.analysis import jsapi, typescript
from repro.config.settings import SettingsError, load_settings
from repro.gitops import Git
from repro.ids import new_evidence_id
from repro.jsmap import JsChange, Reason, Selected, Span
from repro.orchestrator import Job, JobContext, OnFailure
from repro.provision import deps as dependencies
from repro.provision import environment_missing
from repro.readable import reason
from repro.run_stage import (
    CBOR,
    DOCKER_FIX,
    SANDBOX_FIX,
    TEST_RUN_TIMEOUT_S,
    TESTS_TIMEOUT_S,
    Backend,
    RunState,
    emit_selection_graph,
    invariants_job,
)
from repro.runner import observations
from repro.runner.behaviour import MAX_GROUPS, Difference
from repro.runner.outcomes import RERUNS, OutcomeDiff, classify, is_flip, unstable_observation
from repro.runner.pytest import read_results
from repro.runner.testdiff import TestChange
from repro.runner.trace import MAX_SAMPLES
from repro.runner.trace import samples as trace_samples
from repro.sandbox import DockerSandbox, Limits, ProcessSandbox, SandboxError
from repro.sandbox.image import BUILD_TIMEOUT_S, node_image
from repro.store import ArtifactStore

#: What would let a JavaScript side be installed.
INSTALL_FIX: Final = (
    "Make the project installable from its lockfile with lifecycle scripts off (npm ci "
    "--ignore-scripts, or pnpm or Yarn with --frozen-lockfile), with Node.js and the package "
    "manager on PATH"
)
#: Why head's network use is unseen at L2 (``invariants.no_network``'s ``unseen``).
NETWORK_UNSEEN: Final = (
    "a Node process's network use is neither blocked nor seen in the process sandbox (L2); run "
    "with the Docker sandbox (sandbox = docker) to have it blocked"
)
#: Why head's attempts are not seen at L3, where the network is blocked
#: (``invariants.no_network``'s ``undetected``).
NODE_UNDETECTED: Final = (
    "Repro's detection is a Python audit hook, which a Node process does not load"
)
#: An image of the user's is refused for JavaScript (J04's ``sandbox_image``).
NO_ENV_IMAGE: Final = (
    "sandbox_image is for Python projects: a JavaScript project runs in Repro's Node.js image "
    "(sandbox = docker) or in the process sandbox"
)
#: What would let the TypeScript compiler be used (K11).
TYPESCRIPT_FIX: Final = (
    f"Put Node.js on PATH and let Repro fetch TypeScript {typescript.VERSION} from the npm "
    "registry once (into the data directory's tools)"
)
#: An ``expect`` or ``assert`` call in a test's own code, comments and strings blanked.
_ASSERTION: Final = re.compile(r"\b(?:expect|assert)\s*[.(]")
#: At most this many tests are listed as one missing item's targets.
LISTED: Final = 20
#: What base's version of an edited test file is called beside head's (``base_copy``).
BASE_COPY: Final = "repro-base"
#: The two sides install one after the other. Two installs at once share the package manager's
#: store, and the version a project pins is fetched on first use: on a machine's first run,
#: pnpm's head install failed with "The system cannot find the path specified" while base's
#: ran (Windows, pnpm 12 switching to a project's pnpm 10, 2026-10-20); one at a time, both ran.
_INSTALL: Final = threading.Lock()


@dataclass(frozen=True)
class JsOptions:
    """How the JavaScript runs are made: ``install`` the dependencies (off where ``node_modules``
    is in place already, as in Repro's own tests), ``entry``, the runner's entry point instead of
    the project's own (a test's Vitest), and ``tools``, where Repro keeps the tools it fetches
    (the pinned TypeScript compiler; ``<data dir>/tools`` when None)."""

    install: bool = True
    entry: str | None = None
    tools: Path | None = None


def typescript_package(options: JsOptions) -> Path:
    """The pinned TypeScript compiler's package, fetched into the tools directory on first use.
    Raises ``typescript.TypeScriptError``."""
    tools = options.tools
    if tools is None:
        try:
            tools = load_settings().paths.data_dir / "tools"
        except SettingsError as exc:
            raise typescript.TypeScriptError(f"the data directory is not known: {exc}") from None
    return typescript.ensure(tools)


@dataclass
class JsState(RunState):
    """What the JavaScript jobs hand on, besides ``RunState``'s (the policy, the evidence the
    invariants read)."""

    changes: list[JsChange] = field(default_factory=list)
    js_projects: dict[str, jsts.JsProject] = field(default_factory=dict)
    #: Where each side's tests run: the host's copy (L2) or a container's volume (L3, K11).
    trees: dict[str, jsts.Tree] = field(default_factory=dict)
    #: The packages installed on each side, by name, with their version.
    installed: dict[str, dict[str, str]] = field(default_factory=dict)
    #: The traced runs (A, A2, H) and the reruns of flipped tests on each side.
    runs: dict[str, jsts.Side] = field(default_factory=dict)
    reruns: dict[str, list[jsts.Side]] = field(default_factory=dict)
    traced: dict[str, list[str]] = field(default_factory=dict)
    selected: dict[str, Selected] = field(default_factory=dict)
    #: The tests whose own code changed, and their lines on each side.
    own: dict[str, Reason] = field(default_factory=dict)
    blocks: dict[str, tuple[Span | None, Span | None]] = field(default_factory=dict)
    #: The changes with the tests' own lines taken out (``jsmap.own_changes``).
    effective: list[JsChange] = field(default_factory=list)
    diffs: list[OutcomeDiff] = field(default_factory=list)
    test_changes: list[TestChange] = field(default_factory=list)
    #: Why edited tests' assertions were counted by a scanner, not read from the syntax tree.
    assertions_unread: str | None = None
    #: What the tests job found missing: the runs, the maps, the files that did not run.
    problems: list[dict[str, Any]] = field(default_factory=list)
    #: Each edited test's outcome when base's version runs against head's code (K11).
    base_on_head: dict[str, str | None] = field(default_factory=dict)


def _snapshot(work: Path, side: str) -> Path:
    return work / "snapshots" / side


# -- sandboxes ------------------------------------------------------------------------------------


def _sandbox_job(
    git: Git,
    side: str,
    sha: str,
    work: Path,
    state: JsState,
    *,
    backend: Backend,
    limits: Limits | None,
    docker_runtime: str | None,
    env_image: str | None,
    options: JsOptions,
) -> Job:
    def run(ctx: JobContext) -> None:
        try:
            provision(ctx)
        except jsts.JsError as exc:
            ctx.log(f"{side}: no environment: {exc}", level="warning", status="warn")
            payload = environment_missing(side, str(exc), INSTALL_FIX)
            ctx.emit("missing.recorded", status="warn", payload=payload)
        except SandboxError as exc:  # H01: the sandbox itself, not the project
            ctx.log(f"{side}: no sandbox: {exc}", level="warning", status="warn")
            detail = f"the sandbox could not start: {reason(exc)}"
            fix = DOCKER_FIX if backend == "docker" else SANDBOX_FIX
            payload = environment_missing(side, detail, fix)
            ctx.emit("missing.recorded", status="warn", payload=payload)

    def provision(ctx: JobContext) -> None:
        if env_image is not None:
            raise jsts.JsError(NO_ENV_IMAGE)
        if backend == "e2b":  # K09 built E2B for Python changes
            raise SandboxError("the E2B sandbox runs Python changes only for now (K09)")
        project = jsts.JsProject.detect(_snapshot(work, side))
        chosen = limits or Limits(timeout_s=TEST_RUN_TIMEOUT_S)
        where = _Where(side, work, state, options, chosen)
        if backend == "docker":
            took, node = _in_container(ctx, where, project, git, sha, runtime=docker_runtime)
        else:
            took, node = _on_host(ctx, where, project)
        ctx.check()
        payload = {
            "side": side,
            "project_kind": project.manager,
            "python_version": None,
            "node_version": node,
            "lock_sha256": jsts.lock_sha256(project),
            "installed_packages": len(state.installed[side]),
            "cache_hit": False,
            "duration_ms": max(0, int(took * 1000)),
        }
        state.js_projects[side] = project
        ctx.emit("env.provisioned", status="ok", payload=payload)

    return Job(
        f"sandbox-{side}",
        "build",
        run,
        OnFailure(
            reason="environment_failed",
            detail=f"the {side} environment could not be provisioned",
            resolution=INSTALL_FIX,
        ),
        needs=(f"snapshot-{side}",),
        # At L3, the image may be built first.
        timeout_s=jsts.INSTALL_TIMEOUT_S + (BUILD_TIMEOUT_S if backend == "docker" else 0) + 60,
    )


@dataclass(frozen=True)
class _Where:
    """One side's provisioning: the side, the run's directory and state, how, the limits."""

    side: str
    work: Path
    state: JsState
    options: JsOptions
    limits: Limits

    def installing(self, ctx: JobContext, project: jsts.JsProject) -> None:
        ctx.log(
            f"{self.side}: installing the dependencies ({project.manager} from its lockfile, "
            "lifecycle scripts off)"
            if self.options.install
            else f"{self.side}: dependencies in place, not installed"
        )


def _on_host(ctx: JobContext, where: _Where, project: jsts.JsProject) -> tuple[float, str]:
    """The process sandbox (L2), the install on the host: the time it took, and Node.js."""
    side, state, chosen = where.side, where.state, where.limits
    node = jsts.node_version()
    # V8 reserves address space far beyond what it commits (jsts.MEMORY_BYTES).
    box = ProcessSandbox(
        where.work / "sandboxes" / side,
        replace(chosen, memory_bytes=max(chosen.memory_bytes, jsts.MEMORY_BYTES)),
    )
    ready = box.ready_payload(side)
    ready["platform"] = f"{platform.platform()} Node.js {node}"[:200]
    state.network[side] = ready["network"]
    state.platform[side] = f"process {ready['platform']}"
    ctx.emit("sandbox.ready", status="ok", payload=ready)
    where.installing(ctx, project)
    took = 0.0
    if where.options.install:
        with _INSTALL:
            started = time.monotonic()
            jsts.install_dependencies(project)
            took = time.monotonic() - started
    state.installed[side] = jsts.installed(project.root)
    state.trees[side] = jsts.HostTree(project.root, box)
    return took, node


def _in_container(
    ctx: JobContext,
    where: _Where,
    project: jsts.JsProject,
    git: Git,
    sha: str,
    *,
    runtime: str | None,
) -> tuple[float, str]:
    """A container sandbox on the Node.js image (L3, K11), the install in a container of its
    own: the time it took, and Node.js."""
    side, state = where.side, where.state
    if where.options.entry is not None:
        raise jsts.JsError("a runner on the host (JsOptions.entry) cannot run in a container")
    image = node_image()
    ctx.check()
    # Docker limits the memory used, not the address space V8 reserves: no jsts.MEMORY_BYTES.
    box = DockerSandbox(
        where.work / "sandboxes" / side, git, sha, image=image, limits=where.limits, runtime=runtime
    )
    state.containers.append(box)
    state.platform[side] = f"docker {image}"
    ready = box.ready_payload(side)
    state.network[side] = ready["network"]
    ctx.emit("sandbox.ready", status="ok", payload=ready)
    where.installing(ctx, project)
    took = 0.0
    if where.options.install:  # no lock: each install has its own container and caches
        started = time.monotonic()
        jsts.install_in_container(box, project)
        took = time.monotonic() - started
    ctx.check()
    node, state.installed[side] = jsts.container_environment(box, project)
    state.trees[side] = jsts.VolumeTree(box)
    return took, node


# -- tests ----------------------------------------------------------------------------------------


def _order(run_id: str) -> dict[str, Any]:
    """``environment`` evidence: how base's two runs are ordered, for the card's conditions."""
    seed = int(jstrace.SEED)
    return {
        "id": new_evidence_id(),
        "run_id": run_id,
        "type": "environment",
        "targets": [{"file": "."}],
        "summary": (
            f"base runs twice: in the runner's own order, then shuffled with seed {seed}; head "
            "in the runner's own order"
        ),
        "data": {"shuffle_seed": seed},
        "artifacts": [],
        "stability": "n/a",
        "origin": "n/a",
        "produced_by": {"component": "repro.js_stage", "version": __version__},
    }


def _file(test: str) -> str:
    return test.split("::", 1)[0]


def outcome(side: jsts.Side, test: str) -> str | None:
    """A test's outcome in a run; ``failed`` when its file did not run there, None when the run
    does not have it."""
    found = side.outcomes.get(test)
    if found is None and _file(test) in side.broken:
        return "failed"
    return found


def _read(work: Path, side: str, path: str | None) -> str | None:
    if path is None:
        return None
    try:
        return (_snapshot(work, side) / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def own_code(
    work: Path, changes: Sequence[JsChange], base: jsts.Side, head: jsts.Side
) -> tuple[dict[str, Reason], dict[str, tuple[Span | None, Span | None]], list[JsChange]]:
    """The tests whose own code the change edits (``new_test`` when base has none), their lines
    on each side, and the changes with those lines taken out (``jsmap.own_changes``)."""
    own: dict[str, Reason] = {}
    blocks: dict[str, tuple[Span | None, Span | None]] = {}
    effective = []
    for change in changes:
        found: list[dict[str, Span]] = []
        for side, run, path in (("base", base, change.old_path), ("head", head, change.new_path)):
            starts = {t: s for t, s in run.starts.items() if path is not None and _file(t) == path}
            source = _read(work, side, path) if starts else None
            found.append(jsmap.own_lines(source, starts) if source is not None else {})
        if not found[0] and not found[1]:
            effective.append(change)
            continue
        edited, rest = jsmap.own_changes(change, found[0], found[1])
        effective.append(rest)
        for test in sorted(edited):
            own[test] = "modified_test" if test in base.outcomes else "new_test"
            blocks[test] = (found[0].get(test), found[1].get(test))
    return own, blocks, effective


def _ran_changed(test: str, maps: Sequence[jsmap.TestMap], changes: Sequence[JsChange]) -> bool:
    """Whether a test ran any changed code (DeFlaker's check, E-less: by the map); True when
    the map does not have it."""
    ran = [m.ran[test] for m in maps if test in m.ran]
    if not ran:
        return True
    return any(
        file == c.file and (name in c.functions or c.module_level)
        for found in ran
        for file, name in found
        for c in changes
    )


class _Events:
    """``test.started`` / ``test.finished`` per reported test and attempt, as runs end."""

    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        self.attempts: dict[tuple[str, str], int] = {}

    def run(self, side: str, found: jsts.Side, tests: Sequence[str]) -> None:
        for test in tests:
            result = outcome(found, test)
            if result is None:
                continue
            key = (side, test)
            self.attempts[key] = attempt = self.attempts.get(key, 0) + 1
            target = {"test": test}
            payload: dict[str, Any] = {"side": side, "attempt": attempt}
            self.ctx.emit("test.started", status="progress", payload=payload, target=target)
            finished = {**payload, "outcome": result, "duration_ms": found.durations.get(test, 0)}
            status = "ok" if result == "passed" else "warn"
            self.ctx.emit("test.finished", status=status, payload=finished, target=target)

    def samples(self, side: str, found: jsts.Side, names: Mapping[Any, str]) -> None:
        listed = trace_samples(read_results(b"", found.calls), names, side)
        for payload, target in listed:
            self.ctx.emit("trace.sample", status="progress", payload=payload, target=target)
        if len(listed) >= MAX_SAMPLES:
            self.ctx.log(f"{side}: samples stopped at {MAX_SAMPLES}", level="warning")


def _run_problems(label: str, side: str, found: jsts.Side) -> list[dict[str, Any]]:
    """Missing items for one run: a run that did not finish, a map not made, test files that
    did not run."""
    items: list[dict[str, Any]] = []
    if found.problem or found.ended != "exited":
        detail = f"{side} ({label}): " + (found.problem or f"the run ended: {found.ended}")
        items.append(_missing("run_incomplete", detail, [], "Make the suite run to its end"))
    if found.map is not None and found.map.problem:
        detail = f"{side} ({label}): the test map was not made: {found.map.problem}"
        items.append(_missing("trace_unavailable", detail, [], None))
    for file, message in sorted(found.broken.items()):
        detail = f"{side} ({label}): {file} did not run: {message}"
        items.append(_missing("collection_error", detail, [{"file": file}], None))
    return items


def _missing(
    why: str, detail: str, targets: Sequence[Mapping[str, str]], resolution: str | None
) -> dict[str, Any]:
    return {
        "missing": {
            "reason": why,
            "targets": list(targets),
            "detail": detail[:1000],
            "resolution": resolution,
        }
    }


def _tests_job(work: Path, state: JsState, options: JsOptions, commits: Mapping[str, str]) -> Job:
    def run(ctx: JobContext) -> None:
        if "base" not in state.js_projects or "head" not in state.js_projects:
            ctx.log("tests not run: a side has no environment", level="warning", status="warn")
            return

        def stop() -> bool:
            return ctx.cancelled

        state.traced = traced = jstrace.plan(state.changes)
        names = jstrace.targets(traced)
        if traced:
            ctx.log(f"tracing {len(names)} changed functions on both sides")
        ctx.emit("evidence.created", status="ok", payload={"evidence": _order(ctx.run_id)})

        def tests(side: str, label: str, **how: Any) -> jsts.Side:
            found = jsts.run_tests(
                state.js_projects[side],
                state.trees[side],
                work / "runs" / f"{side}-{label}".replace(" ", "-"),
                commits[side],
                entry=options.entry,
                stop=stop,
                **how,
            )
            ctx.log(
                f"{side} {label}: {len(found.outcomes)} tests reported"
                + (f" ({found.problem})" if found.problem else "")
            )
            state.problems.extend(_run_problems(label, side, found))
            ctx.check()
            return found

        a = state.runs["A"] = tests("base", "A", trace=traced)
        h = state.runs["H"] = tests("head", "H", trace=traced)
        if a.problem or h.problem:
            return
        state.own, state.blocks, state.effective = own_code(work, state.changes, a, h)
        maps = [m for m in (a.map, h.map) if m is not None]
        state.selected = jsmap.select(state.effective, maps, state.own)
        ctx.log(f"selected {len(state.selected)} tests of {len(a.outcomes | h.outcomes)}")
        changed = state.analysis.changes.changes if state.analysis is not None else ()
        chosen = ((test, why.symbols) for test, why in state.selected.items())
        emit_selection_graph(ctx, chosen, changed)
        for test, why in state.selected.items():
            payload = {"reasons": list(why.reasons), "symbols": list(why.symbols)}
            ctx.emit("test.selected", status="ok", payload=payload, target={"test": test})
        both = sorted(a.outcomes.keys() | h.outcomes.keys())
        reported = [t for t in both if t in state.selected or outcome(a, t) != outcome(h, t)]
        events = _Events(ctx)
        for side, found in (("base", a), ("head", h)):
            events.run(side, found, reported)
            if traced:
                events.samples(side, found, names)
        a2 = state.runs["A2"] = tests("base", "A2", trace=traced, again=True, mapped=False)
        events.run("base_repeat", a2, reported)
        if traced:
            events.samples("base_repeat", a2, names)
        flips = [
            t
            for t in reported
            if is_flip(
                outcome(a, t),
                outcome(a2, t),
                outcome(h, t),
                modified=state.own.get(t) == "modified_test",
            )
        ]
        files = sorted({_file(t) for t in flips})
        if files:
            ctx.log(f"{len(flips)} tests flipped: running them again {RERUNS} times each side")
        for n in range(1, RERUNS + 1) if files else ():
            for side in ("base", "head"):
                found = tests(side, f"rerun {n}", mapped=False, files=files)
                state.reruns.setdefault(side, []).append(found)
                events.run(side, found, flips)
        state.diffs = [_classify(t, state, maps) for t in reported]
        edited = sorted(
            t
            for t, kind in state.own.items()
            if kind == "modified_test" and outcome(a, t) == "passed" and t in h.outcomes
        )
        if edited:
            ctx.log(f"running base's version of {len(edited)} edited tests against head's code")
            state.base_on_head = _base_tests_on_head(work, state, edited, tests)
        state.test_changes = _test_changes(work, state, options)
        _network(state)

    return Job(
        "tests",
        "run",
        run,
        OnFailure(reason="environment_failed", detail="the tests could not be run on both sides"),
        needs=("sandbox-base", "sandbox-head", "static-analysis"),
        timeout_s=TESTS_TIMEOUT_S,
    )


def _network(state: JsState) -> None:
    """What ``no_network`` can say of head's runs: at L3 the network was cut off, and Node's
    attempts are not seen; at L2 it was neither cut off nor seen."""
    if state.network.get("head") == "enforced":
        state.head_network, state.network_undetected = [], NODE_UNDETECTED
    else:
        state.network_unseen = NETWORK_UNSEEN


def base_copy(path: str) -> str:
    """Where base's version of the test file ``path`` goes in head's tree (K11): beside it, so
    its relative imports reach head's code, and named so the runners' default patterns still
    match it (``m.test.ts`` becomes ``m.repro-base.test.ts``)."""
    name = PurePosixPath(path)
    for marker in (".test.", ".spec."):
        at = name.name.find(marker)
        if at >= 0:
            return str(name.with_name(f"{name.name[:at]}.{BASE_COPY}{name.name[at:]}"))
    return str(name.with_name(f"{name.stem}.{BASE_COPY}{name.suffix}"))


def _base_tests_on_head(
    work: Path,
    state: JsState,
    edited: Sequence[str],
    tests: Callable[..., jsts.Side],
) -> dict[str, str | None]:
    """Each edited test's outcome when base's version of its file runs in head's tree: the
    regression an edit to the test can hide (Python's ``base tests on head``, D06). A file
    whose copy would replace one of the project's is not run, and its tests have no outcome.
    The copies are the head tree's last run; on the host they are removed after it."""
    tree = state.trees["head"]
    copies = {file: base_copy(file) for file in sorted({_file(test) for test in edited})}
    files: dict[str, bytes] = {}
    for file, copy in copies.items():
        source = _snapshot(work, "base") / file
        if source.is_file() and tree.read(tree.root / copy) is None:
            files[copy] = source.read_bytes()
    try:
        tree.write(files)
        found = (
            tests("head", "base tests on head", mapped=False, files=sorted(files))
            if files
            else None
        )
    finally:
        if isinstance(tree, jsts.HostTree):
            for copy in files:
                (tree.root / copy).unlink(missing_ok=True)
    result: dict[str, str | None] = {}
    for test in edited:
        file, _, name = test.partition("::")
        result[test] = None if found is None else outcome(found, f"{copies[file]}::{name}")
    return result


def _classify(test: str, state: JsState, maps: Sequence[jsmap.TestMap]) -> OutcomeDiff:
    a, a2, h = state.runs["A"], state.runs["A2"], state.runs["H"]
    return classify(
        test,
        base=outcome(a, test),
        base_again=None if a2.problem else outcome(a2, test),
        head=outcome(h, test),
        modified=state.own.get(test) == "modified_test",
        base_reruns=[outcome(r, test) for r in state.reruns.get("base", [])],
        head_reruns=[outcome(r, test) for r in state.reruns.get("head", [])],
        ran_changed_code=_ran_changed(test, maps, state.effective),
        base_message=a.messages.get(test) or a.broken.get(_file(test)),
        head_message=h.messages.get(test) or h.broken.get(_file(test)),
    )


def _assertions(source: str | None, span: Span | None) -> int | None:
    if source is None or span is None:
        return None
    lines = jsmap.blanked(source).split("\n")[span[0] - 1 : span[1]]
    return len(_ASSERTION.findall("\n".join(lines)))


Assertions = tuple[jsassert.Assertion, ...]


def _shapes(
    work: Path,
    state: JsState,
    edited: Sequence[str],
    sources: Mapping[tuple[str, str], str | None],
    options: JsOptions,
) -> dict[str, tuple[Assertions, Assertions]] | None:
    """Each edited test's assertions on base and on head, from the compiler's syntax tree; None
    (and ``state.assertions_unread``) if the compiler could not be used."""
    files: list[tuple[str, jsassert.Source]] = []
    for index, side in enumerate(("base", "head")):
        spans: dict[str, dict[str, Span]] = {}
        for test in edited:
            span = state.blocks.get(test, (None, None))[index]
            if span is not None and sources[(side, _file(test))] is not None:
                spans.setdefault(_file(test), {})[test] = span
        for file, found in spans.items():
            files.append((side, jsassert.Source(file, sources[(side, file)] or "", found)))
    if not files:
        return {}
    try:
        package = typescript_package(options)
        read = jsassert.assertions(package, [s for _, s in files], work / "jsassert")
    except typescript.TypeScriptError as exc:
        state.assertions_unread = str(exc)
        return None
    sides: dict[str, dict[str, Assertions]] = {"base": {}, "head": {}}
    for (side, _), tests in zip(files, read, strict=True):
        sides[side].update(tests)
    return {
        test: (sides["base"][test], sides["head"][test])
        for test in edited
        if test in sides["base"] and test in sides["head"]
    }


def _test_changes(work: Path, state: JsState, options: JsOptions) -> list[TestChange]:
    """Removed tests, then each edited test's findings: skipped on head; else fewer, weaker or
    wider assertions (``jsassert``); else only changed. Without the compiler, fewer ``expect``
    or ``assert`` calls are counted by a scanner (K08's)."""
    a, h = state.runs["A"], state.runs["H"]
    found = [
        TestChange(_file(test), test, "test_removed", "not reported on head")
        for test in sorted(a.outcomes.keys() - h.outcomes.keys())
        if _file(test) not in h.broken
    ]
    edited = [t for t, kind in state.own.items() if kind == "modified_test" and t in h.outcomes]
    sources: dict[tuple[str, str], str | None] = {}
    for test in edited:
        for side in ("base", "head"):
            sources.setdefault((side, _file(test)), _read(work, side, _file(test)))
    shapes = _shapes(work, state, edited, sources, options)
    for test in edited:
        file = _file(test)
        if a.outcomes[test] != "skipped" and h.outcomes[test] == "skipped":
            found.append(TestChange(file, test, "skip_added", "skipped on head; it ran on base"))
            continue
        if shapes is not None:
            findings = jsassert.compare(*shapes[test]) if test in shapes else []
        else:
            before, after = state.blocks.get(test, (None, None))
            counts = (
                _assertions(sources[("base", file)], before),
                _assertions(sources[("head", file)], after),
            )
            findings = []
            if counts[0] is not None and counts[1] is not None and counts[1] < counts[0]:
                detail = f"{counts[0]} expect or assert calls on base, {counts[1]} on head"
                findings.append(("assertion_removed", detail))
        found += [TestChange(file, test, kind, detail) for kind, detail in findings] or [
            TestChange(file, test, "test_modified", "the test's code changed")
        ]
    for test, got in sorted(state.base_on_head.items()):
        if got is not None and got != "passed":
            detail = f"base's version passes on base, {got} on head"
            found.append(TestChange(_file(test), test, "base_test_fails_on_head", detail))
    return found


# -- outcomes -------------------------------------------------------------------------------------


def _uncovered(state: JsState) -> list[dict[str, Any]]:
    """``no_covering_test`` for each changed symbol outside test files that no selected test
    reaches."""
    analysis = state.analysis
    if analysis is None:
        return []
    test_files = {_file(t) for run in state.runs.values() for t in run.outcomes}
    reached = {symbol for why in state.selected.values() for symbol in why.symbols}
    items = []
    for change in analysis.changes.changes:
        if change.file in test_files or change.symbol_id in reached:
            continue
        detail = f"no selected test runs {change.symbol_id} ({change.change})"
        targets = [{"file": change.file, "symbol": change.symbol_id}]
        items.append(_missing("no_covering_test", detail, targets, "Add a test that runs it"))
    return items


def _edited(edited: Sequence[str], state: JsState) -> str | None:
    """What was not checked of the edited tests, if anything: their assertions' kinds when the
    TypeScript compiler could not be used, and base's versions that did not run on head."""
    parts = []
    if state.assertions_unread is not None:
        parts.append(
            "their assertions were counted by a scanner, as the TypeScript compiler could not "
            f"be used ({state.assertions_unread}): a weakened or wider assertion is not checked"
        )
    unrun = [test for test in edited if test in state.base_on_head and not state.base_on_head[test]]
    if unrun:
        parts.append(f"base's version of {len(unrun)} of them did not run against head's code")
    return f"{len(edited)} edited tests: " + "; ".join(parts) if parts else None


def _outcomes_job(state: JsState) -> Job:
    def run(ctx: JobContext) -> None:
        for item in state.problems:
            ctx.emit("missing.recorded", status="warn", payload=item)
        if not state.diffs and "H" not in state.runs:
            ctx.log("no outcomes: the tests did not run", level="warning", status="warn")
            return
        for diff in state.diffs:
            record = diff.evidence(ctx.run_id)
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            ctx.emit(
                "diff.outcome",
                status=diff.status,  # ADR-0015 §2
                payload=diff.payload(record["id"]),
                target={"test": diff.test},
            )
        for change in state.test_changes:
            record = change.evidence(ctx.run_id)
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            target = {"file": change.file, **({"test": change.test} if change.test else {})}
            payload = change.payload(record["id"])
            ctx.emit("diff.tests", status=change.status, payload=payload, target=target)
        missing = [unstable_observation(diff) for diff in state.diffs if not diff.stable]
        ran = [state.runs.get(label) for label in ("A", "H")]
        if all(run is not None and not run.problem for run in ran):
            missing += _uncovered(state)
        edited = sorted(t for t, kind in state.own.items() if kind == "modified_test")
        unchecked = _edited(edited, state) if edited else None
        if unchecked is not None:
            targets = [{"test": test} for test in edited[:LISTED]]
            missing.append(_missing("unsupported_project", unchecked, targets, None))
        missing.append(
            _missing(
                "perf_not_assessable",
                "JavaScript tests are not timed: performance is compared for Python (D07)",
                [],
                None,
            )
        )
        if set(state.network.values()) != {"enforced"}:  # L2; at L3 the network is cut off
            missing.append(
                _missing(
                    "network_not_enforced",
                    f"the network: {NETWORK_UNSEEN}",
                    [],
                    "Run with the Docker sandbox (sandbox = docker): the tests then run with no "
                    "network",
                )
            )
        for item in missing:
            ctx.emit("missing.recorded", status="warn", payload=item)
        counts: dict[str, int] = {}
        for diff in state.diffs:
            counts[diff.outcome_class] = counts.get(diff.outcome_class, 0) + 1
        summary = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) or "no outcomes"
        ctx.log(f"outcomes: {summary}; {len(state.test_changes)} test changes")

    return Job(
        "outcomes",
        "diff",
        run,
        OnFailure(reason="environment_failed", detail="the outcomes could not be compared"),
        needs=("tests",),
    )


# -- behaviour ------------------------------------------------------------------------------------


def _never_traced(state: JsState, names: Mapping[Any, str]) -> list[dict[str, Any]]:
    """A changed function a test ran, by the map, with no call traced in any run: a nested
    function, an object literal's method, or (Jest) one called from inside its module."""
    traced = {
        target
        for label in ("A", "A2", "H")
        for target in {c.target for c in read_results(b"", state.runs[label].calls).calls}
    }
    maps = [m for m in (state.runs["A"].map, state.runs["H"].map) if m is not None]
    ran = {pair for m in maps for found in m.ran.values() for pair in found}
    items = []
    for target, symbol in sorted(names.items(), key=lambda item: item[1]):
        file, name = target[0], target[1]
        if (file, name) in ran and target not in traced:
            detail = (
                f"{symbol} ran in the tests, but none of its calls was traced: a nested "
                "function, an object literal's method, or (Jest) a call from inside its module"
            )
            items.append(_missing("trace_unavailable", detail, [{"symbol": symbol}], None))
    return items


def _behaviour_job(state: JsState, artifacts: ArtifactStore | None) -> Job:
    def run(ctx: JobContext) -> None:
        labels = ("A", "A2", "H")
        if not state.traced:
            ctx.log("no behaviour comparison: no changed function to trace")
            return
        if any(label not in state.runs or state.runs[label].problem for label in labels):
            detail = "the traced calls were not compared: a run of A, A2 and H did not finish"
            item = _missing("trace_unavailable", detail, [], None)
            ctx.emit("missing.recorded", status="warn", payload=item)
            ctx.log(detail, level="warning", status="warn")
            return
        names = jstrace.targets(state.traced)
        modified = frozenset(t for t, kind in state.own.items() if kind == "modified_test")
        found, listed = jstrace.behaviour(
            [(state.runs[label].outcomes, state.runs[label].calls) for label in labels],
            names,
            modified,
        )
        state.behaviour = found
        counts = found.counts()
        ctx.log(
            f"behaviour: {counts['calls']} calls compared, {counts['same']} the same, "
            f"{counts['unstable']} unstable, {counts['unknown']} not comparable, "
            f"{counts['differences']} differences"
        )
        cited: list[dict[str, Any]] = []
        if artifacts is not None:
            refs = {}
            for observed in found.runs.values():
                data = observations.dump_observations(observed)
                ref = artifacts.put_bytes(ctx.run_id, f"obs/{observed.label}.cbor", data, CBOR)
                refs[observed.label] = {"path": ref.path, "sha256": ref.sha256}
            data = observations.dump_behaviour(found, refs)
            cited.append(
                artifacts.put_bytes(ctx.run_id, "obs/behaviour.cbor", data, CBOR).model_dump()
            )
        for item in observations.missing(found) + _never_traced(state, names):
            ctx.emit("missing.recorded", status="warn", payload=item)
        if state.js_projects.get("base") and state.js_projects["base"].runner == "jest":
            detail = (
                "Jest: calls through a module's exports are traced; a call from inside the "
                "module is not (K07)"
            )
            item = _missing("unsupported_project", detail, [], None)
            ctx.emit("missing.recorded", status="warn", payload=item)
        itemised: list[Difference] = listed[:MAX_GROUPS]
        for difference in itemised:
            record = difference.evidence(ctx.run_id, cited)
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            ctx.emit(
                "diff.behaviour",
                status="warn",  # ADR-0015 §2: every behaviour difference needs a claim
                payload=difference.payload(record["id"]),
                target={"symbol": difference.symbol, "test": difference.test},
            )
            if difference.stability == "unstable":
                ctx.emit("missing.recorded", status="warn", payload=difference.missing())
        if len(listed) > len(itemised):
            ctx.log(
                f"{len(listed) - len(itemised)} more behaviour differences are in "
                "obs/behaviour.cbor, not itemised",
                level="warning",
            )

    return Job(
        "behaviour",
        "diff",
        run,
        OnFailure(reason="trace_unavailable", detail="the traced calls could not be compared"),
        needs=("tests",),
        timeout_s=TESTS_TIMEOUT_S,
    )


# -- api and dependencies -------------------------------------------------------------------------


def _api_job(
    git: Git, base: str, head: str, work: Path, state: JsState, *, options: JsOptions
) -> Job:
    def run(ctx: JobContext) -> None:
        try:
            package = typescript_package(options)
            found = jsapi.api_diff(git, base, head, work / "jsapi", package)
        except typescript.TypeScriptError as exc:
            detail = f"the public API was not compared: {exc}"
            item = _missing("unsupported_project", detail, [], TYPESCRIPT_FIX)
            ctx.emit("missing.recorded", status="warn", payload=item)
            ctx.log(detail, level="warning", status="warn")
            state.api_breaks, state.api_unread = None, []
            return
        breaks = []
        for change in found.breaks:
            record = change.evidence(ctx.run_id)
            breaks.append((change.symbol, change.breakage, str(record["id"])))
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            target = {"symbol": change.symbol, **({"file": change.file} if change.file else {})}
            payload = change.payload(record["id"])
            ctx.emit("diff.api", status="warn", payload=payload, target=target)  # ADR-0015 §2
        for missing in found.missing():
            ctx.emit("missing.recorded", status="warn", payload=missing)
        entries = ", ".join(found.packages) or "no entry point"
        ctx.log(
            f"public API ({entries}; TypeScript {typescript.VERSION}): "
            f"{len(found.breaks)} breaking changes"
        )
        ctx.check()  # an abandoned job's comparison is not the run's
        state.api_breaks = breaks
        state.api_unread = sorted({name for _, name, _ in found.problems})

    # Read from git alone, as Python's: compared even when a side could not be installed.
    return Job(
        "api",
        "diff",
        run,
        OnFailure(reason="unsupported_project", detail="the public API could not be compared"),
        after=("tests",),
    )


def _deps_job(work: Path, state: JsState) -> Job:
    def run(ctx: JobContext) -> None:
        trees = {side: _snapshot(work, side) for side in ("base", "head")}
        declared = {side: jsts.declared(tree) for side, tree in trees.items()}
        unread = [side for side, found in declared.items() if found is None]
        for side in unread:
            detail = f"{side}: package.json cannot be read: a dependency it adds is not seen"
            item = _missing("unsupported_project", detail, [{"file": "package.json"}], None)
            ctx.emit("missing.recorded", status="warn", payload=item)
        changes: list[dependencies.DepChange] = []
        if declared["base"] is not None and declared["head"] is not None:
            changes += dependencies.compare(
                declared["base"], declared["head"], "declared", "package.json"
            )
        if {"base", "head"} <= state.js_projects.keys():  # read where they were installed
            changes += dependencies.compare(
                state.installed["base"], state.installed["head"], "installed", None
            )
        cited: list[tuple[str, str, str, str]] = []
        for change in changes:
            record = change.evidence(ctx.run_id)
            cited.append((change.package, change.change, change.source, str(record["id"])))
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            target = {"file": change.file or "."}
            payload = change.payload(record["id"])
            ctx.emit("diff.deps", status="warn", payload=payload, target=target)  # ADR-0015 §2
        if any(_workspaces(tree) for tree in trees.values()):
            detail = (
                "the project has workspaces: only the root package.json's dependencies are compared"
            )
            item = _missing("unsupported_project", detail, [{"file": "package.json"}], None)
            ctx.emit("missing.recorded", status="warn", payload=item)
        ctx.check()  # an abandoned job's comparison is not the run's
        state.dep_changes, state.dep_unread = cited, unread
        ctx.log(f"dependencies: {len(changes)} differences")

    return Job(
        "deps",
        "diff",
        run,
        OnFailure(reason="environment_failed", detail="the dependencies could not be compared"),
        after=("tests",),
    )


def _workspaces(tree: Path) -> bool:
    if (tree / "pnpm-workspace.yaml").is_file():
        return True
    try:
        return "workspaces" in jsts.manifest(tree)
    except jsts.JsError:
        return False


# -- the stages -----------------------------------------------------------------------------------


def run_jobs(
    git: Git,
    base: str,
    head: str,
    work: Path,
    state: JsState,
    *,
    backend: Backend,
    artifacts: ArtifactStore | None = None,
    limits: Limits | None = None,
    docker_runtime: str | None = None,
    env_image: str | None = None,
    options: JsOptions | None = None,
) -> Sequence[Job]:
    """The run, diff and check stages of a JavaScript change, after the build stage's
    snapshots, analysis (``state.analysis`` and ``state.changes``) and policy. With
    ``backend="docker"``, in containers (L3, K11; ``docker_runtime``: theirs, H03)."""
    how = options or JsOptions()

    def sandbox(side: str, sha: str) -> Job:
        return _sandbox_job(
            git,
            side,
            sha,
            work,
            state,
            backend=backend,
            limits=limits,
            docker_runtime=docker_runtime,
            env_image=env_image,
            options=how,
        )

    return [
        sandbox("base", base),
        sandbox("head", head),
        _tests_job(work, state, how, {"base": base, "head": head}),
        _outcomes_job(state),
        _behaviour_job(state, artifacts),
        _api_job(git, base, head, work, state, options=how),
        _deps_job(work, state),
        invariants_job(git, base, head, state, after=("api", "deps", "outcomes")),
    ]
