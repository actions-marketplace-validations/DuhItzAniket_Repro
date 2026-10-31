"""The run and diff stages of a run's pipeline (D06): sandboxes, environments, two-sided
execution, and its evidence as events.

- ``sandbox-<side>`` (build): the side's sandbox, Docker (L3) when asked or available, else the
  process sandbox (L2); its environment (D03). ``sandbox.ready``, ``env.provisioned``. A project
  that cannot be provisioned is an ``environment_failed`` item with the reason, and the run goes
  on without running tests: the change is unverifiable, not the run broken.
- ``tests`` (run): ``repro.runner.twosided.reproduce``. ``test.selected`` per selected test,
  after its ``graph.node`` and ``covers`` edges for the stage (``emit_selection_graph``),
  ``test.started`` / ``test.finished`` per test and attempt.
- ``outcomes`` (diff): ``evidence.created`` with ``diff.outcome`` per classified test and
  ``diff.tests`` per test change; what the sandbox detected; ``missing.recorded`` for each
  uncovered symbol, flaky test, unfinished run and collection error.
- ``perf`` (run, D07): the tests that pass on both sides, timed untraced in ABBA blocks;
  ``perf.sample`` per test and run. ``perf-diff`` (diff): ``perf_diff`` evidence with
  ``diff.perf`` per timed test, and a ``perf_not_assessable`` item for those too fast to time.
- ``behaviour`` (diff, E03, E04): the calls A, A2 and H traced, as observations; A against H
  with what differs between A and A2 masked. Each run's observations and the comparison are
  stored as CBOR artifacts (``obs/``); the noise masked (``unstable_observation``), what could
  not be compared (``encoding_failed``) and cut traces (``trace_truncated``) are missing items.
  The differences that remain run again on each side for their reproducers, then become
  ``behaviour_diff`` evidence with a ``diff.behaviour`` each (``warn``, ADR-0015).
- ``api`` (diff, E05): the public API's breaking changes (griffe, statically, plus annotation
  changes), ``api_diff`` evidence with a ``diff.api`` each (``warn``).
- ``deps`` (diff, E05): the requirements and the installed packages of both sides, compared,
  ``dep_diff`` evidence with a ``diff.deps`` each (``warn``); lock drift as ``lock_drift``
  evidence. Both come ``after`` ``tests``, so the diff stage follows the run stage, and run
  however the tests ended (F02): ``api`` reads git alone, and ``deps`` compares the
  requirements read from the snapshots even for a side that could not be installed (the
  installed packages only where there is an environment).
- ``probes`` (run, E06): the changed functions with JSON-typed parameters, called with
  generated inputs on base, base again (other seeds) and head; ``probe-diff`` (diff):
  ``probe_diff`` evidence (``provenance: generated``) with a ``diff.probe`` each (``warn``), the
  probes and their results stored as ``obs/probes.cbor``.

``perf`` waits for every other job that runs the sides (``behaviour``'s reproducers,
``probes``): timing beside them would measure their load.

- ``invariants`` (check, F02, F03): each invariant base's ``repro.yml`` enables, evaluated
  from the run's evidence: ``invariant`` evidence and an ``invariant.result`` each. It comes
  ``after`` ``api``, ``deps``, ``outcomes`` (head's network attempts) and ``perf-diff`` without
  needing them to succeed: an invariant whose evidence they could not produce is
  ``not_evaluated``, and ``forbidden_paths`` needs only git. A ``no_network`` pass where the
  sandbox only detects is a ``network_not_enforced`` item.

The jobs share a ``RunState``; ``RunState.close`` removes what they left (Docker volumes).
"""

import asyncio
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from repro import __version__
from repro.agents.stages import AgentState

if TYPE_CHECKING:
    from repro.agents.setup import AgentSetup
    from repro.agents.sources import RepoSources
from repro.analysis import StaticAnalysis
from repro.analysis.api import api_diff
from repro.analysis.blast import MAX_TEST_NODES, selection_graph
from repro.analysis.changes import SymbolChange
from repro.gitops import Git
from repro.gitops import diff as changed_files
from repro.ids import new_evidence_id
from repro.orchestrator import Job, JobCancelled, JobContext, OnFailure
from repro.policy import FILE as POLICY_FILE
from repro.policy import Invariant, invariants
from repro.policy.stage import PolicyRead
from repro.provision import (
    DockerProvisioner,
    E2BProvisioner,
    HostProvisioner,
    ImageProvisioner,
    Project,
    ProjectError,
    ProvisionError,
    environment_missing,
    inspect_project,
)
from repro.provision import deps as dependencies
from repro.readable import reason
from repro.runner import behaviour as behaviour_diffs
from repro.runner import observations, probes, selection
from repro.runner.observations import Behaviour
from repro.runner.outcomes import unstable_observation
from repro.runner.perf import (
    TIMING_FLOOR_S,
    Samples,
    TimingRun,
    assess,
    not_assessable,
    time_tests,
)
from repro.runner.selection import MapCache, Selected, map_key, no_covering_test
from repro.runner.sides import Side
from repro.runner.trace import MAX_SAMPLES, TracePlan
from repro.runner.trace import plan as trace_plan
from repro.runner.trace import samples as trace_samples
from repro.runner.twosided import (
    OTHER_HASH_SEED,
    OTHER_SEED,
    TRACED_RUNS,
    RunRecord,
    TwoSided,
    reproduce,
)
from repro.sandbox import DockerSandbox, Limits, ProcessSandbox, SandboxError, docker_available
from repro.sandbox.e2b import E2BSandbox, ensure_template
from repro.sandbox.evidence import detection_evidence
from repro.sandbox.image import sandbox_image
from repro.sandbox.process import HASH_SEED
from repro.sandbox.remote import RemoteSandbox
from repro.store import ArtifactStore, cbor

Backend = Literal["auto", "docker", "process", "e2b"]
#: Labels of runs that are not attempts of the selected tests themselves.
_NOT_ATTEMPTS: Final = frozenset({"head tests", "base tests on head"})
PROVISION_TIMEOUT_S: Final = 1800.0
TESTS_TIMEOUT_S: Final = 3600.0
#: A side's probe run at most (a generated input can make a function loop).
PROBE_TIMEOUT_S: Final = 300.0
#: A sandboxed test run's wall-clock limit when the settings give none (H01: test_timeout_s).
TEST_RUN_TIMEOUT_S: Final = 900.0
#: What to do when the sandbox itself cannot start (H01).
DOCKER_FIX: Final = (
    "Start Docker (Docker Desktop on Windows and macOS), or run with the process sandbox "
    "(REPRO_SANDBOX=process); repro doctor says which sandboxes this machine can use"
)
SANDBOX_FIX: Final = "Run repro doctor: it says what this machine is missing"
#: The media type RFC 8949 §9.3 registers, for the observation artifacts.
CBOR: Final = "application/cbor"


@dataclass
class RunState:
    """What the run's jobs hand on to each other."""

    analysis: StaticAnalysis | None = None
    #: Each side's project as read from its snapshot, provisioned or not (D03; F02).
    projects: dict[str, Project] = field(default_factory=dict)
    sides: dict[str, Side] = field(default_factory=dict)
    result: TwoSided | None = None
    platform: dict[str, str] = field(default_factory=dict)
    timings: list[Samples] | None = None
    timing_runs: list[TimingRun] = field(default_factory=list)
    #: Tests under the timing floor in every traced run: too fast to time, never timed (H02).
    too_fast_already: list[str] = field(default_factory=list)
    behaviour: Behaviour | None = None
    probing: probes.Probing | None = None
    #: The policy read from base (F01); None until the policy job ran.
    policy: PolicyRead | None = None
    #: The API's breaks, (symbol, breakage, evidence ID), and the packages not read (E05; F02).
    api_breaks: list[tuple[str, str, str]] | None = None
    api_unread: list[str] = field(default_factory=list)
    #: The dependency differences, (package, change, source, evidence ID), and the sides whose
    #: declared dependencies could not be read (E05; F02).
    dep_changes: list[tuple[str, str, str, str]] | None = None
    dep_unread: list[str] = field(default_factory=list)
    #: Each side's network, as its ``sandbox.ready`` states it (F03).
    network: dict[str, str] = field(default_factory=dict)
    #: Head's network attempts with their evidence, None if the tests did not run, and whether
    #: every detection was read (F03).
    head_network: list[invariants.Attempt] | None = None
    head_network_complete: bool = True
    #: Why head's network use could be neither blocked nor seen, if it could not (K08).
    network_unseen: str | None = None
    #: Why head's network attempts are not seen where the network is blocked, if they are not
    #: (K11: a Node process in a container).
    network_undetected: str | None = None
    #: Each timed test's comparison with its evidence, None if nothing was timed, and the tests
    #: too fast to time (D07; F03).
    perf_timed: list[invariants.Timed] | None = None
    perf_too_fast: list[str] = field(default_factory=list)
    #: Every container sandbox opened, provisioned or not: their volumes go with ``close``.
    containers: list[RemoteSandbox] = field(default_factory=list)
    #: What the agent jobs hand on (G04).
    agents: AgentState = field(default_factory=AgentState)

    def close(self) -> None:
        while self.containers:
            self.containers.pop().close()
        self.sides.clear()


def use_docker(backend: Backend) -> bool:
    return backend == "docker" or (backend == "auto" and docker_available())


#: What to do when E2B has no sandbox for a side (K09).
E2B_FIX: Final = (
    "Start the self-hosted E2B (docs/guide/troubleshooting.md, E2B), and set E2B_API_KEY and "
    "E2B_DOMAIN or E2B_API_URL for it"
)


def _e2b_side(  # noqa: PLR0917 - a side's parts
    ctx: JobContext,
    side: str,
    git: Git,
    sha: str,
    box_dir: Path,
    limits: Limits,
    state: RunState,
    size: tuple[int, int],
) -> E2BSandbox:
    """A side's microVM in E2B (K09), from the template of Repro's image at ``size``; its
    ``sandbox.ready`` emitted."""
    memory_mb, cpus = size
    ctx.check()
    template = ensure_template(memory_mb=memory_mb, cpus=cpus, log=ctx.log)
    vm = E2BSandbox(box_dir, git, sha, template=template, limits=limits)
    state.containers.append(vm)
    state.platform[side] = f"e2b {template}"
    ready = vm.ready_payload(side)
    state.network[side] = ready["network"]
    ctx.emit("sandbox.ready", status="ok", payload=ready)
    return vm


def _sandbox_job(
    git: Git,
    side: str,
    sha: str,
    work: Path,
    state: RunState,
    *,
    backend: Backend,
    cache: Path,
    limits: Limits | None = None,
    docker_runtime: str | None = None,
    env_image: str | None = None,
    e2b_size: tuple[int, int] = (1024, 2),
) -> Job:
    def run(ctx: JobContext) -> None:
        try:
            provision(ctx)
        except (ProjectError, ProvisionError) as exc:
            ctx.log(f"{side}: no environment: {exc}", level="warning", status="warn")
            payload = environment_missing(side, str(exc))
            ctx.emit("missing.recorded", status="warn", payload=payload)
        except SandboxError as exc:  # H01: the sandbox itself, not the project
            ctx.log(f"{side}: no sandbox: {exc}", level="warning", status="warn")
            fix = (
                E2B_FIX if backend == "e2b" else DOCKER_FIX if use_docker(backend) else SANDBOX_FIX
            )  # fmt: skip
            payload = environment_missing(side, f"the sandbox could not start: {reason(exc)}", fix)
            ctx.emit("missing.recorded", status="warn", payload=payload)

    def provision(ctx: JobContext) -> None:
        tree = work / "snapshots" / side
        project = inspect_project(tree)
        state.projects[side] = project
        box_dir = work / "sandboxes" / side
        chosen = limits or Limits(timeout_s=TEST_RUN_TIMEOUT_S)
        provisioning = f"{side}: provisioning the environment ({project.kind})"
        if env_image is not None and not use_docker(backend):
            raise ProvisionError("sandbox_image needs the Docker sandbox (sandbox = docker)")
        if backend == "e2b":
            vm = _e2b_side(ctx, side, git, sha, box_dir, chosen, state, e2b_size)
            ctx.log(provisioning)
            env = E2BProvisioner().provision(side, project, vm)
            state.sides[side] = Side(side, vm, env)
        elif use_docker(backend):
            image = env_image or sandbox_image()
            ctx.check()
            container = DockerSandbox(
                box_dir, git, sha, image=image, limits=chosen, runtime=docker_runtime
            )
            state.containers.append(container)
            state.platform[side] = f"docker {image}"
            ready = container.ready_payload(side)
            state.network[side] = ready["network"]
            ctx.emit("sandbox.ready", status="ok", payload=ready)
            ctx.log(provisioning)
            provisioner = ImageProvisioner(image) if env_image else DockerProvisioner(image)
            env = provisioner.provision(side, project, container)
            state.sides[side] = Side(side, container, env)
        else:
            box = ProcessSandbox(box_dir, chosen)
            state.platform[side] = f"process {sys.platform}"
            ready = box.ready_payload(side)
            state.network[side] = ready["network"]
            ctx.emit("sandbox.ready", status="ok", payload=ready)
            ctx.log(provisioning)
            env = HostProvisioner(cache / "envs").provision(side, project, box)
            state.sides[side] = Side(side, box, env)
        ctx.emit("env.provisioned", status="ok", payload=env.payload())

    return Job(
        f"sandbox-{side}",
        "build",
        run,
        OnFailure(
            reason="environment_failed",
            detail=f"the {side} environment could not be provisioned",
            resolution=(
                "Make the project installable from the package index, with wheels for its "
                "dependencies, and its own build working offline"
            ),
        ),
        needs=(f"snapshot-{side}",),
        timeout_s=PROVISION_TIMEOUT_S,
    )


class _TestEvents:
    """``test.started`` / ``test.finished`` per selected test and attempt, as runs end."""

    def __init__(self, ctx: JobContext, changes: Sequence[SymbolChange]) -> None:
        self.ctx = ctx
        self.changes = changes
        self.attempts: dict[tuple[str, str], int] = defaultdict(int)
        self.plan: TracePlan | None = None  # what A, A2 and H trace: their samples (E07)

    def selected(self, chosen: tuple[Selected, ...]) -> None:
        self.ctx.log(f"selected {len(chosen)} tests")
        emit_selection_graph(self.ctx, ((s.test, s.symbols) for s in chosen), self.changes)
        for item in chosen:
            self.ctx.emit(
                "test.selected", status="ok", payload=item.payload(), target={"test": item.test}
            )

    def observe(self, record: RunRecord) -> None:
        results = record.run.results
        listed = record.label == "head tests"  # collected only, not run
        count = len(results.collected) if listed else len(results.tests)
        self.ctx.log(
            f"{record.side} {record.label}: {count} tests {'collected' if listed else 'reported'}"
            + ("" if results.complete else " (the run did not finish)")
        )
        if record.label in _NOT_ATTEMPTS:
            return
        # The repeat base run (A2: the docs' A-prime) has a side of its own: the cockpit draws it
        # dimmer in the BASE lane, with its own counts.
        side = "base_repeat" if record.label == "A2" else record.side
        for test in record.tests:
            found = results.tests.get(test)
            if found is None:
                continue
            key = (side, test)
            self.attempts[key] += 1
            attempt = self.attempts[key]
            target = {"test": test}
            payload = {"side": side, "attempt": attempt}
            self.ctx.emit("test.started", status="progress", payload=payload, target=target)
            finished = {
                **payload,
                "outcome": found.outcome,
                "duration_ms": max(0, int(found.duration_s * 1000)),
            }
            status = "ok" if found.outcome in ("passed", "xfailed", "xpassed") else "warn"
            self.ctx.emit("test.finished", status=status, payload=finished, target=target)
        self.samples(record, side)

    def samples(self, record: RunRecord, side: str) -> None:
        """``trace.sample`` per previewed call of a traced run (E07): what the behaviour field
        and Signals draw while the run goes."""
        if self.plan is None or record.label not in TRACED_RUNS:
            return
        names = self.plan.head if record.side == "head" else self.plan.base
        found = trace_samples(record.run.results, names, side)
        for payload, target in found:
            self.ctx.emit("trace.sample", status="progress", payload=payload, target=target)
        if len(found) >= MAX_SAMPLES:
            self.ctx.log(f"{side}: samples stopped at {MAX_SAMPLES}", level="warning")


def emit_selection_graph(
    ctx: JobContext,
    selected: Iterable[tuple[str, Iterable[str]]],
    changes: Sequence[SymbolChange],
) -> None:
    """The selected tests as graph nodes, with ``covers`` edges to the changed symbols that
    selected them (``selection_graph``), before their ``test.selected`` events: the points the
    stage's strands fire from (B14). Drawing only: no check reads them."""
    found = selection_graph(selected, changes)
    for kind, payload in found.events:
        ctx.emit(kind, status="ok", payload=payload)
    if found.left_out:
        ctx.log(
            f"{found.left_out} selected tests not drawn on the stage (at most "
            f"{MAX_TEST_NODES} test nodes, those selected for the most changed symbols)"
        )


def _seeds(run_id: str) -> dict[str, Any]:
    """``environment`` evidence: the seeds base's two runs use (A, then A2), for the card's
    conditions (F05)."""
    data = {
        "hash_seed": HASH_SEED,
        "hash_seed_repeat": OTHER_HASH_SEED,
        "seed": 0,
        "seed_repeat": OTHER_SEED,
    }
    return {
        "id": new_evidence_id(),
        "run_id": run_id,
        "type": "environment",
        "targets": [{"file": "."}],
        "summary": (
            f"base runs twice: PYTHONHASHSEED {HASH_SEED} then {OTHER_HASH_SEED}, random seed 0 "
            f"then {OTHER_SEED}; head with base's first"
        ),
        "data": data,
        "artifacts": [],
        "stability": "n/a",
        "origin": "n/a",
        "produced_by": {"component": "repro.runner.twosided", "version": __version__},
    }


def _tests_job(git: Git, base: str, head: str, state: RunState, *, cache: Path) -> Job:
    def run(ctx: JobContext) -> None:
        analysis = state.analysis
        if analysis is None:
            raise RuntimeError("the static analysis left nothing to select from")
        if "base" not in state.sides or "head" not in state.sides:
            ctx.log("tests not run: a side has no environment", level="warning", status="warn")
            return
        sides = state.sides["base"], state.sides["head"]
        key = map_key(
            base,
            sides[0].env.project.lock_sha256,
            sides[0].env.python_version,
            state.platform.get("base", "?"),
        )
        events = _TestEvents(ctx, analysis.changes.changes)
        resolvers = selection.side_resolvers(git, base, head)
        plan = trace_plan(analysis.changes.changes, resolvers)
        events.plan = plan or None
        if plan:
            ctx.log(f"tracing {len(set(plan.base.values()))} changed functions on both sides")
        ctx.emit("evidence.created", status="ok", payload={"evidence": _seeds(ctx.run_id)})
        state.result = reproduce(
            sides,
            analysis.changes.changes,
            (analysis.base, analysis.head),
            resolvers,
            cache=MapCache(cache / "maps"),
            cache_key=key,
            observe=events.observe,
            on_selected=events.selected,
            stop=lambda: ctx.cancelled,
            trace=plan,
        )
        made = (
            "from the cache, made by an earlier run of this base"
            if state.result.map_cached
            else "made by running every base test"
        )
        ctx.log(f"base map: {len(state.result.base_map.tests)} tests, {made}")

    return Job(
        "tests",
        "run",
        run,
        OnFailure(
            reason="environment_failed",
            detail="the tests could not be run on both sides",
        ),
        needs=("sandbox-base", "sandbox-head", "static-analysis"),
        timeout_s=TESTS_TIMEOUT_S,
    )


def _outcomes_job(state: RunState) -> Job:
    def run(ctx: JobContext) -> None:
        result = state.result
        if result is None:
            ctx.log("no outcomes: the tests did not run", level="warning", status="warn")
            return
        for diff in result.outcomes:
            record = diff.evidence(ctx.run_id)
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            ctx.emit(
                "diff.outcome",
                status=diff.status,  # ADR-0015 §2
                payload=diff.payload(record["id"]),
                target={"test": diff.test},
            )
        for change in result.test_changes:
            record = change.evidence(ctx.run_id)
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            target = {"file": change.file, **({"test": change.test} if change.test else {})}
            payload = change.payload(record["id"])
            ctx.emit("diff.tests", status=change.status, payload=payload, target=target)
        attempts: list[invariants.Attempt] = []
        for side in ("base", "head"):
            fallback = {"file": "."}
            for record in detection_evidence(
                result.detections(side), run_id=ctx.run_id, side=side, fallback_target=fallback
            ):
                ctx.emit("evidence.created", status="warn", payload={"evidence": record})
                if side == "head" and record["type"] == "network_attempt":
                    data = record["data"]
                    attempts.append(invariants.Attempt(data["event"], data["detail"], record["id"]))
        state.head_network = attempts
        state.head_network_complete = all(
            not r.run.execution.detections.truncated and not r.run.execution.detections.dropped
            for r in result.runs
            if r.side == "head"
        )
        missing = [no_covering_test(gap) for gap in result.gaps]
        missing += [unstable_observation(diff) for diff in result.outcomes if not diff.stable]
        limit = next((s.sandbox.limits.memory_bytes for s in state.sides.values()), None)
        missing += result.problems(limit)
        for item in missing:
            ctx.emit("missing.recorded", status="warn", payload=item)
        counts: dict[str, int] = defaultdict(int)
        for diff in result.outcomes:
            counts[diff.outcome_class] += 1
        summary = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) or "no outcomes"
        ctx.log(f"outcomes: {summary}; {len(result.test_changes)} test changes")

    return Job(
        "outcomes",
        "diff",
        run,
        OnFailure(reason="environment_failed", detail="the outcomes could not be compared"),
        needs=("tests",),
    )


def too_fast_to_time(result: TwoSided, tests: Sequence[str]) -> list[str]:
    """The tests whose call took under the timing floor in every traced run that reported it
    (A, A2 and H; H02). Tracing only slows a call down, so the untraced timing runs would find
    them under the floor too: timing them would only say so, at many runs' cost."""
    runs = result.traced().values()
    fast = []
    for test in tests:
        seen = [
            found.call_s
            for record in runs
            if (found := record.run.results.tests.get(test)) is not None
        ]
        if seen and all(call is not None and call < TIMING_FLOOR_S for call in seen):
            fast.append(test)
    return fast


def _perf_job(state: RunState) -> Job:
    def run(ctx: JobContext) -> None:
        result = state.result
        if result is None or "base" not in state.sides or "head" not in state.sides:
            ctx.log("no timing: the tests did not run", level="warning", status="warn")
            return
        passing = [d.test for d in result.outcomes if d.outcome_class == "P2P"]
        state.too_fast_already = fast = too_fast_to_time(result, passing)
        stable = [test for test in passing if test not in set(fast)]
        if not stable:
            why = (
                "every selected test that passes on both sides is too fast to time"
                if passing
                else "no selected test passes on both sides"
            )
            ctx.log(f"no timing: {why}")
            state.timings = []
            return
        already = f"; {len(fast)} under the timing floor in every run already" if fast else ""
        ctx.log(f"timing {len(stable)} tests, untraced, base and head interleaved (ABBA){already}")

        def observe(timing: TimingRun) -> None:
            for test in stable:
                found = timing.record.results.tests.get(test)
                if found is None or found.call_s is None:
                    continue
                payload = {
                    "side": timing.side,
                    "repetition": timing.repetition,
                    "duration_ms": found.call_s * 1000,
                }
                ctx.emit("perf.sample", status="progress", payload=payload, target={"test": test})

        state.timings, state.timing_runs = time_tests(
            state.sides["base"],
            state.sides["head"],
            stable,
            observe=observe,
            stop=lambda: ctx.cancelled,
        )

    return Job(
        "perf",
        "run",
        run,
        OnFailure(reason="perf_not_assessable", detail="the timing runs did not complete"),
        needs=("tests", "behaviour", "probes"),  # alone: the others' runs would load its timing
        timeout_s=TESTS_TIMEOUT_S,
    )


def _perf_diff_job(state: RunState) -> Job:
    def run(ctx: JobContext) -> None:
        if state.timings is None:
            ctx.log("no performance comparison: nothing was timed")
            return
        for timing in state.timing_runs:
            if not timing.record.results.complete:
                detail = (
                    f"{timing.side}: timing run {timing.repetition} did not finish; "
                    "its samples are missing"
                )
                payload: dict[str, Any] = {
                    "missing": {
                        "reason": "perf_not_assessable",
                        "targets": [],
                        "detail": detail,
                        "resolution": None,
                    }
                }
                ctx.emit("missing.recorded", status="warn", payload=payload)
        compared, assessed = assess(state.timings)
        fast = {*assessed, *state.too_fast_already}
        order = [d.test for d in state.result.outcomes] if state.result is not None else []
        too_fast = [test for test in order if test in fast]
        timed: list[invariants.Timed] = []
        for diff in compared:
            record = diff.evidence(ctx.run_id)
            timed.append(
                invariants.Timed(
                    diff.test, diff.verdict, diff.ratio, diff.ci_low, diff.ci_high, record["id"]
                )
            )
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            status = "warn" if diff.verdict == "regression" else "ok"
            ctx.emit(
                "diff.perf",
                status=status,
                payload=diff.payload(record["id"]),
                target={"test": diff.test},
            )
        if too_fast:
            ctx.emit("missing.recorded", status="warn", payload=not_assessable(too_fast))
        ctx.check()  # an abandoned job's comparison is not the run's
        state.perf_timed, state.perf_too_fast = timed, list(too_fast)
        verdicts = ", ".join(f"{d.test}: {d.verdict}" for d in compared[:10]) or "none timed"
        ctx.log(f"performance: {verdicts}; {len(too_fast)} too fast to time")

    return Job(
        "perf-diff",
        "diff",
        run,
        OnFailure(reason="perf_not_assessable", detail="the timings could not be compared"),
        needs=("perf",),
    )


def _behaviour_job(state: RunState, artifacts: ArtifactStore | None) -> Job:
    def run(ctx: JobContext) -> None:
        result = state.result
        behaviour = observations.behaviour_of(result) if result is not None else None
        if result is None or behaviour is None:
            ctx.log("no behaviour comparison: nothing traced on all of A, A2 and H")
            return
        state.behaviour = behaviour
        counts = behaviour.counts()
        ctx.log(
            f"behaviour: {counts['calls']} calls compared, {counts['same']} the same, "
            f"{counts['unstable']} unstable, {counts['unknown']} not comparable, "
            f"{counts['differences']} differences"
        )
        modified = frozenset(s.test for s in result.selected if "modified_test" in s.reasons)
        found = behaviour_diffs.differences(behaviour, modified)
        itemised = found[: behaviour_diffs.MAX_GROUPS]
        again = behaviour_diffs.Reproduction(None, None)
        if itemised and behaviour.plan is not None and {"base", "head"} <= state.sides.keys():
            tests = len({d.test for d in itemised})
            ctx.log(f"reproducing {len(itemised)} behaviour differences: {tests} tests again")
            again = behaviour_diffs.reproduce(
                itemised,
                (state.sides["base"], state.sides["head"]),
                behaviour.plan,
                stop=lambda: ctx.cancelled,
                timeout_s=TESTS_TIMEOUT_S,
            )
        cited: list[dict[str, Any]] = []
        if artifacts is not None:
            refs = {}
            runs = [*behaviour.runs.values(), *(r for r in (again.base, again.head) if r)]
            for observed in runs:
                data = observations.dump_observations(observed)
                ref = artifacts.put_bytes(ctx.run_id, f"obs/{observed.label}.cbor", data, CBOR)
                refs[observed.label] = {"path": ref.path, "sha256": ref.sha256}
            data = observations.dump_behaviour(behaviour, refs)
            ref = artifacts.put_bytes(ctx.run_id, "obs/behaviour.cbor", data, CBOR)
            cited.append(ref.model_dump())
        for item in observations.missing(behaviour):
            ctx.emit("missing.recorded", status="warn", payload=item)
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
        if len(found) > len(itemised):
            ctx.log(
                f"{len(found) - len(itemised)} more behaviour differences are in "
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


def _api_job(git: Git, base: str, head: str, work: Path, state: RunState) -> Job:
    def run(ctx: JobContext) -> None:
        found = api_diff(git, base, head, work / "api")
        breaks = []
        for item in found.breaks:
            record = item.evidence(ctx.run_id)
            breaks.append((item.symbol, item.breakage, str(record["id"])))
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            target = {"symbol": item.symbol, **({"file": item.file} if item.file else {})}
            payload = item.payload(record["id"])
            ctx.emit("diff.api", status="warn", payload=payload, target=target)  # ADR-0015 §2
        for missing in found.missing():
            ctx.emit("missing.recorded", status="warn", payload=missing)
        packages = ", ".join(found.packages) or "no packages"
        ctx.log(f"public API ({packages}): {len(found.breaks)} breaking changes")
        ctx.check()  # an abandoned job's comparison is not the run's
        state.api_breaks = breaks
        state.api_unread = sorted({package for _, package, _ in found.problems})

    # Read from git alone: compared even when a side could not be installed or tested, after
    # the tests so that the diff stage follows the run stage.
    return Job(
        "api",
        "diff",
        run,
        OnFailure(reason="unsupported_project", detail="the public API could not be compared"),
        after=("tests",),
    )


def _deps_job(state: RunState) -> Job:
    def run(ctx: JobContext) -> None:
        if "base" not in state.projects or "head" not in state.projects:
            ctx.log("no dependency comparison: a side's project was not read", level="warning")
            return
        base, head = state.projects["base"], state.projects["head"]
        changes = dependencies.compare(
            dependencies.project_requirements(base),
            dependencies.project_requirements(head),
            dependencies.source_of(head),
            head.source or base.source,
        )
        changes += dependencies.declared_changes(base, head)
        unread = [
            side for side in ("base", "head") if dependencies.undeclared(state.projects[side])
        ]
        for side in unread:
            ctx.emit("missing.recorded", status="warn", payload=_undeclared(side, state))
        listed = _installed(ctx, state)
        if len(listed) == 2:  # noqa: PLR2004 - both sides
            changes += dependencies.compare(listed["base"], listed["head"], "installed", None)
        cited: list[tuple[str, str, str, str]] = []
        for change in changes:
            record = change.evidence(ctx.run_id)
            cited.append((change.package, change.change, change.source, str(record["id"])))
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            target = {"file": change.file or "."}
            payload = change.payload(record["id"])
            ctx.emit("diff.deps", status="warn", payload=payload, target=target)  # ADR-0015 §2
        ctx.check()  # an abandoned job's comparison is not the run's
        state.dep_changes, state.dep_unread = cited, unread
        drift = [
            item
            for side, found in listed.items()
            for item in dependencies.lock_drift(side, state.projects[side], found)
        ]
        for item in drift:
            ctx.emit(
                "evidence.created", status="warn", payload={"evidence": item.evidence(ctx.run_id)}
            )
        ctx.log(f"dependencies: {len(changes)} differences, {len(drift)} drifted from the lock")

    # The requirements are read from the snapshots: compared even when a side could not be
    # installed (a dependency that does not install is still one the change adds); the
    # installed packages only where there is an environment.
    return Job(
        "deps",
        "diff",
        run,
        OnFailure(reason="environment_failed", detail="the dependencies could not be compared"),
        after=("tests",),
    )


def _undeclared(side: str, state: RunState) -> dict[str, Any]:
    project = state.projects[side]
    where = project.declared_file or "the project's metadata"
    return {
        "missing": {
            "reason": "unsupported_project",
            "targets": [{"file": project.declared_file}] if project.declared_file else [],
            "detail": (
                f"{side}: the dependencies {where} declares cannot be read statically (computed "
                "by the build, or not literal), and no lock pins them: a dependency added there "
                "is not seen"
            ),
            "resolution": "Declare the dependencies statically, or lock them",
        }
    }


def _installed(ctx: JobContext, state: RunState) -> dict[str, dict[str, str]]:
    """Each side's installed packages, where it has an environment (a side without one has its
    missing item already); a missing item for a side whose could not be listed."""
    listed: dict[str, dict[str, str]] = {}
    for name in ("base", "head"):
        side = state.sides.get(name)
        if side is None:
            continue
        found = side.installed()
        if found is None:
            detail = f"{name}: the installed packages could not be listed"
            payload: dict[str, Any] = {
                "missing": {
                    "reason": "environment_failed",
                    "targets": [],
                    "detail": detail,
                    "resolution": None,
                }
            }
            ctx.emit("missing.recorded", status="warn", payload=payload)
        else:
            listed[name] = found
    return listed


def _proposals(
    ctx: JobContext,
    found: Sequence[probes.Candidate],
    setup: "AgentSetup",
    *,
    sources: "RepoSources",
) -> list[list[dict[str, Any]]] | None:
    """The probe writer's inputs for ``found`` (G07), or None when it gave none; a failure is
    an ``agent_failed`` item, and the probes run on Hypothesis's inputs alone."""
    from repro.agents import prober  # noqa: PLC0415
    from repro.agents.cassette import CassetteError  # noqa: PLC0415 - agents only when on
    from repro.agents.events import run_agent  # noqa: PLC0415
    from repro.agents.prompts import load_prompt  # noqa: PLC0415

    request = prober.prober_request(found, sources, load_prompt("probe"), setup.model("probe"))
    agent = setup.dispatch.agent_id()
    ctx.log(f"asking the probe writer for inputs ({setup.label}, {request.model})")
    try:
        result = asyncio.run(
            run_agent(ctx, setup.backend, setup.dispatch, request, agent=agent, ledger=setup.ledger)
        )
        detail = f"the probe writer ended {result.outcome}: {result.detail}"
    except CassetteError as exc:  # Hypothesis's inputs still run: the job is not lost
        result, detail = None, f"the probe writer's call has no usable cassette: {exc}"
    except JobCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - H01: whatever ends the agent, the probes still run
        result, detail = None, f"the probe writer could not run: {reason(exc)}"
    if result is None or result.outcome != "ok":
        item: dict[str, Any] = {
            "reason": "agent_failed",
            "targets": [
                {"file": c.file, "symbol": c.symbol} for c in found[: prober.MAX_FUNCTIONS]
            ],
            "detail": detail[:1000],
            "resolution": "check the agent settings, the API or the cassettes, then run again",
        }
        ctx.emit("missing.recorded", status="warn", payload={"missing": item})
        return None
    kept = prober.read_proposals(result.structured, found)
    ctx.log(
        f"probe writer: {kept.proposed} inputs proposed, {kept.dropped} dropped as not fitting "
        "their function's signature"
    )
    return kept.inputs


def _probes_job(
    git: Git, base: str, head: str, state: RunState, agents: "AgentSetup | None" = None
) -> Job:
    def run(ctx: JobContext) -> None:
        analysis = state.analysis
        if analysis is None or "base" not in state.sides or "head" not in state.sides:
            ctx.log("no probes: the sides did not run", level="warning")
            return
        resolvers = selection.side_resolvers(git, base, head)
        found = probes.candidates(analysis.changes.changes, resolvers)
        if not found:
            ctx.log("no probes: no changed function has JSON-typed parameters on both sides")
            return
        names = ", ".join(c.symbol for c in found[:5]) + (" …" if len(found) > 5 else "")  # noqa: PLR2004
        ctx.log(f"probing {len(found)} functions with generated inputs: {names}")
        proposed = None
        if agents is not None and agents.probes:
            from repro.agents.sources import RepoSources  # noqa: PLC0415 - agents only when on

            proposed = _proposals(ctx, found, agents, sources=RepoSources(git, base, head))
        state.probing = probes.probe(
            (state.sides["base"], state.sides["head"]),
            found,
            proposed=proposed,
            timeout_s=PROBE_TIMEOUT_S,
            stop=lambda: ctx.cancelled,
        )
        if proposed is not None:
            counts = state.probing.proposed
            ctx.log(
                f"proposed inputs: {counts.get('ran', 0)} ran, {counts.get('unstable', 0)} left "
                f"out as unstable, {counts.get('differing', 0)} differ between base and head"
            )

    return Job(
        "probes",
        "run",
        run,
        OnFailure(reason="environment_failed", detail="the generated probes could not be run"),
        needs=("tests",),
        timeout_s=TESTS_TIMEOUT_S,
    )


def _probe_diff_job(state: RunState, artifacts: ArtifactStore | None) -> Job:
    def run(ctx: JobContext) -> None:
        found = state.probing
        if found is None:
            ctx.log("no probe comparison: nothing was probed")
            return
        cited: list[dict[str, Any]] = []
        if artifacts is not None:
            data = cbor.encode(found.document())
            cited.append(
                artifacts.put_bytes(ctx.run_id, "obs/probes.cbor", data, CBOR).model_dump()
            )
        for difference in found.differences:
            record = difference.evidence(ctx.run_id, cited)
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            ctx.emit(
                "diff.probe",
                status="warn",  # ADR-0015 §2: every difference needs a claim to explain it
                payload=difference.payload(record["id"]),
                target={"symbol": difference.candidate.symbol, "file": difference.candidate.file},
            )
        for item in found.missing():
            ctx.emit("missing.recorded", status="warn", payload=item)
        tried = sum(len(i) for i in found.inputs)
        generated = sum(found.generated) if found.generated else tried
        inputs = (
            f"{tried} generated inputs"
            if generated == tried
            else f"{tried} inputs ({generated} generated, {tried - generated} proposed)"
        )
        ctx.log(
            f"probes: {inputs} over {len(found.candidates)} functions, "
            f"{len(found.differences)} differences, {sum(found.unstable.values())} unstable"
        )

    return Job(
        "probe-diff",
        "diff",
        run,
        OnFailure(reason="environment_failed", detail="the probes could not be compared"),
        needs=("probes",),
    )


def invariants_job(
    git: Git,
    base: str,
    head: str,
    state: RunState,
    after: Sequence[str] = ("api", "deps", "outcomes", "perf-diff"),
) -> Job:
    def run(ctx: JobContext) -> None:
        read = state.policy
        if read is None or read.policy is None:
            ctx.log(f"no invariants evaluated: {POLICY_FILE} on base is not valid", status="warn")
            return
        if not read.policy.invariants:
            ctx.log("no invariants: none enabled by the base policy")
            return
        touched = sorted(
            {p for c in changed_files(git, base, head) for p in (c.old_path, c.new_path) if p}
        )
        results = [_evaluate(invariant, state, touched) for invariant in read.policy.invariants]
        for result in results:
            record = result.evidence(ctx.run_id)
            ctx.emit("evidence.created", status="ok", payload={"evidence": record})
            ctx.emit(
                "invariant.result",
                status=result.status,  # ADR-0015 §2
                payload=result.payload(record["id"]),
                target={"file": POLICY_FILE},
            )
            if (result.invariant, result.result) == ("no_network", "pass") and (
                result.details.get("network") != "enforced"
            ):
                payload = invariants.network_not_enforced()
                ctx.emit("missing.recorded", status="warn", payload=payload)
        summary = ", ".join(f"{r.invariant} {r.result}" for r in results)
        ctx.log(f"invariants: {summary}")

    return Job(
        "invariants",
        "check",
        run,
        OnFailure(reason="unsupported_project", detail="the invariants could not be evaluated"),
        needs=("policy",),
        # Whatever these find, or "not evaluated" for an invariant if they found nothing.
        after=tuple(after),
    )


def _evaluate(
    invariant: Invariant, state: RunState, touched: Sequence[str]
) -> invariants.InvariantResult:
    """One invariant's result, from what the jobs before it left in ``state``."""
    match invariant.name:
        case "public_api_stable":
            return invariants.public_api_stable(invariant, state.api_breaks, state.api_unread)
        case "no_new_dependencies":
            return invariants.no_new_dependencies(invariant, state.dep_changes, state.dep_unread)
        case "forbidden_paths":
            return invariants.forbidden_paths(invariant, touched)
        case "no_network":
            return invariants.no_network(
                invariant,
                state.head_network,
                network=state.network.get("head"),
                complete=state.head_network_complete,
                unseen=state.network_unseen,
                undetected=state.network_undetected,
            )
        case _:  # perf_budget: the schema knows no other
            return invariants.perf_budget(invariant, state.perf_timed, state.perf_too_fast)


def run_jobs(
    git: Git,
    base: str,
    head: str,
    work: Path,
    state: RunState,
    *,
    backend: Backend,
    cache: Path,
    artifacts: ArtifactStore | None = None,
    agents: "AgentSetup | None" = None,
    limits: Limits | None = None,
    docker_runtime: str | None = None,
    env_image: str | None = None,
    e2b_size: tuple[int, int] = (1024, 2),
) -> Sequence[Job]:
    """The run and diff stages, after the build stage's snapshots and analysis; ``artifacts``
    stores the observations (without it they stay in memory); with ``agents``, the probe writer
    proposes inputs for the probes (G07); ``limits``: each sandboxed run's (H01);
    ``docker_runtime``: the containers' runtime (H03); ``env_image``: an image whose own
    environment the tests run in (J04)."""

    def sandbox(side: str, sha: str) -> Job:
        return _sandbox_job(
            git,
            side,
            sha,
            work,
            state,
            backend=backend,
            cache=cache,
            limits=limits,
            docker_runtime=docker_runtime,
            env_image=env_image,
            e2b_size=e2b_size,
        )

    return [
        sandbox("base", base),
        sandbox("head", head),
        _tests_job(git, base, head, state, cache=cache),
        _outcomes_job(state),
        _behaviour_job(state, artifacts),
        _api_job(git, base, head, work, state),
        _deps_job(state),
        _probes_job(git, base, head, state, agents),
        _probe_diff_job(state, artifacts),
        _perf_job(state),
        _perf_diff_job(state),
        invariants_job(git, base, head, state),
    ]
