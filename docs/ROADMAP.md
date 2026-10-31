# Repro — Roadmap

> Baseline v1 (phase A01, 2026-10-13). Supersedes the original M0–M8 milestone list (mapping
> at the end). The process is in [`process/SDLC.md`](process/SDLC.md): each phase ends with a
> phase record, a merged PR and a pushed `main`, and each stage ends with a gate, full CI and a
> tagged release.

**Status legend:** `planned` · `spec` · `in progress` · `done` · `dropped`
**Size:** S ≈ ≤1 day · M ≈ 1–3 days · L ≈ 3–5 days of focused work (L phases may be split)

## Overview

| Stage | Name | Phases | Release | Outcome |
|---|---|---|---|---|
| **A** | Foundation | A01–A08 | v0.1.0 | Monorepo, schema-as-contract, CI on Windows + Linux, risks measured |
| **B** | Cockpit on fixtures | B01–B15 | v0.2.0 | Complete animated cockpit driven only by a recorded event log |
| **C** | Engine core & live stream | C01–C08 | v0.3.0 | `repro run` shows a real repo's graph and blast radius live |
| **D** | Sandboxed two-sided execution | D01–D07 | v0.4.0 | Selected tests run on base and head in isolation, with outcome, test-change and perf evidence |
| **E** | Behaviour evidence | E01–E07 | v0.5.0 | A silent behaviour change is caught with no failing test |
| **F** | Policy & deterministic card | F01–F05 | v0.6.0 | Invariants and a complete card with no LLM involved |
| **G** | Agents | G01–G10 | v0.7.0 | Claims extracted, verdicts judged against cited evidence, generated probes |
| **H** | Hardening & first release | H01–H06 | **v1.0.0** | Installable, documented, hardened local tool |
| **I** | GitHub integration | I01–I04 | v1.1.0 (I01–I03), v1.4.0 (I04) | PR URL in, check run + sticky comment + signed attestation out |
| **J** | Evaluation & research | J01–J10 | v1.2.0 (harness); the study later | Reproducible benchmark results and paper submission |
| **K** | Expansion | K01–K11 | v1.2.0 (K01–K05), v1.3.0 (K06–K08, K11), v1.4.0 (K10), v1.5.0 (K09) | Built |

76 scheduled phases (A–J) plus 11 in Stage K. Stages run in order. B and C could run in
parallel with two contributors, because B depends only on the schema (A04/A05).

```
A ──► B ──────────────┐
  └─► C ──► D ──► E ──► F ──► G ──► H ──► I ──► J
```

---

## Stage A — Foundation · v0.1.0

**Goal:** a reproducible, cross-platform workspace where the event schema is the single source
of truth. Measure the riskiest technical unknowns before building on them.

**Gate A (passed 2026-10-13, v0.1.0, record: [`phases/A-gate.md`](phases/A-gate.md)):** CI is
green on `ubuntu-latest` and `windows-latest` from a clean clone. Changing the
schema regenerates TS and Pydantic types, and CI fails on drift. Spike results are recorded in
ADRs or phase records.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| A01 | Research, architecture & plan baseline | M | — | done |
| A02 | Toolchain & monorepo scaffold | M | A01 | done |
| A03 | Code-quality gates | S | A02 | done |
| A04 | Event schema v1 | M | A02 | done |
| A05 | Schema codegen & drift gate | M | A04 | done |
| A06 | CI pipeline & repository security | M | A03, A05 | done |
| A07 | Test-harness foundations & golden-log tooling | M | A05 | done |
| A08 | Feasibility spikes | M | A02 | done |

**A01 · Research, architecture & plan baseline**
- Deliverables: verified research notes (`docs/research/`), research synthesis, product
  definition, architecture, ADRs 0001–0012, threat model, test strategy, this roadmap,
  implementation plan, SDLC process, paper tracker, and repository hygiene files.
- Accept: all docs are cross-linked. Every external claim is tagged and sourced. Decisions that
  need the owner are listed as `proposed` ADRs. No personal data appears in the repo or in
  commit metadata.

**A02 · Toolchain & monorepo scaffold**
- Deliverables: pnpm workspace (`apps/web`, `packages/events`), uv workspace (`engine`,
  `bench`), pinned toolchain (`.python-version`, `packageManager`, `engines.node ≥ 22.12`),
  `.env.example`, cross-platform task entry points (pnpm scripts and `uv run` tasks; no bash
  in the critical path). CLAUDE.md Commands section filled in.
- Accept: on a clean clone on Windows and Linux, `pnpm install && uv sync` succeeds and
  `pnpm test` / `uv run pytest` run an empty-but-real suite. The interpreter is pinned
  explicitly; uv's default is not relied on.

**A03 · Code-quality gates**
- Deliverables: Ruff (lint + format), a strict type checker for the engine (mypy or pyright,
  chosen and recorded), Biome + `tsc --noEmit` strict for TS, optional pre-commit config, and
  `.editorconfig` alignment.
- Accept: one command per language reports zero issues on the scaffold. A deliberately broken
  file fails each gate (checked in a test or documented in the phase record).

**A04 · Event schema v1**
- Deliverables: JSON Schema 2020-12 for the envelope (core fields plus ADR-0003 fields if
  approved) and one schema per v1 kind (ARCHITECTURE §5.2). Evidence, claim, verdict and card
  schemas. A draft-07, `$ref`-free variant generator for agent output schemas. Schema docs.
- Accept: every kind has a schema with at least one valid and one invalid example, validated in
  tests. An "every kind has a schema" test passes.

**A05 · Schema codegen & drift gate**
- Deliverables (revised in A05, see ADR-0014): an in-house generator (`repro-events-codegen`)
  emitting TS types and strict Pydantic v2 models, both with a discriminated union over `kind`.
  Equivalence tests across JSON Schema, Pydantic and Ajv (TypeScript), plus round trip.
- Accept: `pnpm gen` / `uv run repro-events-codegen` regenerate deterministically, and CI fails
  on any diff between committed and regenerated types. Round-trip passes for all example events.

**A06 · CI pipeline & repository security**
- Deliverables: GitHub Actions workflow for lint, types, unit tests, schema drift and secret
  scanning (gitleaks) on an Ubuntu + Windows matrix. Dependency review and licence allow-list
  check. Caching for uv and pnpm. Branch-protection settings documented. Dependabot or Renovate.
- Accept: a PR with a fake secret or a disallowed licence fails CI. A clean PR passes on both
  OSes in under 10 minutes.

**A07 · Test-harness foundations & golden-log tooling**
- Deliverables: pytest + Hypothesis and Vitest + fast-check configured. JSONL event-log reader
  and validator CLI (`repro-events validate`). Log normaliser that strips IDs and timestamps.
  Golden-file approval helper. Scaffold of the scripted fixture-repo builder (TEST_STRATEGY §3).
- Accept: a sample log validates, normalises to a stable golden file, and the same golden
  passes on Windows and Linux (line endings and paths included).

**A08 · Feasibility spikes** (throwaway code; findings are the deliverable)
- Deliverables:
  1. `sys.monitoring` overhead microbenchmark: no tool / PY_START+DISABLE / local
     PY_START+PY_RETURN with argument encoding, on 3.12, 3.13 and 3.14.
  2. Docker sandbox round trip on Windows (Docker Desktop/WSL2) and Linux: snapshot → volume
     → `--network none` → pytest → results out, timed.
  3. uv environment provisioning inside a container for a medium repo, cold and warm, timed.
- Accept: numbers recorded with method and hardware class. ADR-0004 and ADR-0005 are updated
  with measured facts. Go/no-go and initial default budgets are written down.

---

## Stage B — Cockpit on fixtures · v0.2.0

**Goal:** the complete visual product, driven only by a recorded event log, built to the
DESIGN.md spec and performance budget.

**Gate B (passed 2026-10-14. Re-opened the same day: the owner's review against a sharper copy
of the reference found that the stage still did not match it, and B14 and B15 were added; the
owner approved the look the same day and v0.2.0 was tagged. Record:
[`phases/B-gate.md`](phases/B-gate.md); the mid-laptop 60 fps check is still owed by the owner):** side by side with the reference frame
at 6 s, it reads as the same visual family.
The reference log replays at ×1 at 60 fps on a mid laptop (measured, with the method recorded).
Scrubbing and all speeds work. All panels are driven only by events. Visual-regression,
allocation and bundle-size gates are active in CI.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| B01 | Deterministic fixture generator & reference log | M | A07 | done |
| B02 | EventStore, projections, snapshots & clock | M | B01 | done |
| B03 | App shell, tokens, fonts, header & progress | M | B02 | done |
| B04 | Stage foundation & performance harness | M | B03 | done |
| B05 | Dependency-web layer & bundles | L | B04 | done |
| B06 | Ring layer (file-card atlas) | M | B04 | done |
| B07 | Event-stream layer & callouts | M | B04 | done |
| B08 | Panels I: feed, execution, agent workspace, behaviour field | L | B02, B03 | done |
| B09 | Panels II: change routes, signals, dispatch, change×check index | L | B08 | done |
| B10 | Replay controls & motion safety | M | B05–B09 | done |
| B11 | Focus, evidence drawer & accessibility | M | B10 | done |
| B12 | UI quality gate | M | B11 | done |
| B13 | Visual parity with the reference (added before Gate B) | S | B12 | done |
| B14 | Reference parity II: the stage composition (added at the owner's Gate B review) | M | B13 | done |
| B15 | Stage settings and the whole wheel (added at the owner's seventh B14 review) | M | B14 | done |

**B01 · Deterministic fixture generator & reference log**
- Deliverables: a seeded generator (TS or Python) that produces a realistic run (1 PR, 4 claims,
  12 agents, about 150 files, 28 checks, 4 flagged, about 2–4k events) using only v1 kinds.
  Committed `apps/web/fixtures/demo-run.jsonl` and a stress fixture (10k edges, 2k concurrent
  streak events).
- Accept: same seed gives a byte-identical log. Both logs validate against the schema. Counts
  match the spec in a test.

**B02 · EventStore, projections, snapshots & clock**
- Deliverables: a columnar EventStore (typed arrays plus a payload array, gap detection), pure
  folded projections, snapshots every N events, seek, WindowIndex, and a presentation clock
  (live / replay / pause / speed).
- Accept: property tests pass, namely `fold(all) == fold(snapshot_k) + rest` for all k and
  random-seek equality with a linear fold. Header counters and the index footer come from one
  projection. Unknown kinds are tolerated.

**B03 · App shell, tokens, fonts, header & progress**
- Deliverables: Vite + Preact 11 + signals shell, CSS tokens from DESIGN.md, self-hosted
  JetBrains Mono variable woff2 with `OFL.txt`, layout grid (header, stage, 2×4 panels;
  < 1100px rule), header counters and the 7-segment stage progress bar.
- Accept: the layout matches DESIGN.md dimensions at 1440×900 and 1920×1080. Counters update
  from the fixture. The font licence file ships with the font.

**B04 · Stage foundation & performance harness**
- Deliverables: three.js `WebGLRenderer` host, `Layer` interface, `frame(t)` loop, adaptive DPR
  (≤ 2), perf HUD (frame-time percentiles, draw calls, instance counts), GPU-free render-model
  golden-test harness, and a stress-fixture benchmark run on a mid laptop.
- Accept: the empty stage plus HUD runs. Render-model hashes are stable across runs. A stress
  benchmark is recorded and decides whether the layer designs need changing before B05–B07.

**B05 · Dependency-web layer & bundles**
- Deliverables: layout worker (d3-force, node-hash-seeded positions, cached by graph hash),
  instanced anti-aliased edge quads with recency alpha in the shader, node markers for the
  blast radius, and Bézier bundles (8–20 curves, about 1.2 s fade) triggered by events.
- Accept: 10k edges at the frame budget on the stress fixture. The layout is identical across
  reloads. Bundles are pure functions of `(event, t)` (golden-tested).

**B06 · Ring layer (file-card atlas)**
- Deliverables: atlas worker (OffscreenCanvas 2D → ImageBitmap) from file contents, instanced
  card quads, closed-form rotation and 500 ms ease-out snap to the focused file, ticks and path
  labels.
- Accept: one draw call for the cards. Snap timing matches the spec in golden tests. The atlas
  builds off the main thread.

**B07 · Event-stream layer & callouts**
- Deliverables: an instanced streak ring buffer (≤ 2k live) with velocity-stretched tails and
  state tints, plus tooltip callouts (at most 2 visible).
- Accept: 2k streaks at the frame budget. Tint mapping follows DESIGN.md. Streak state at any
  `t` is reproducible after a scrub.

**B08 · Panels I** (investigation feed, execution lanes, agent workspace, behaviour diff field)
- Accept: each panel is a pure `draw(ctx, view, t)` that redraws only when dirty. View-model
  unit tests and per-panel screenshot tests pass.

**B09 · Panels II** (change routes, signals, agents/dispatch, change×check index)
- Accept: same as B08. The index footer and header counters can never disagree (single-projection
  test). Signals show the "illustrative until run completes" caption while partial.

**B10 · Replay controls & motion safety**
- Deliverables: transport controls (play/pause, ×0.5/×1/×4/×16, scrubber, jump to live).
  Global pause with Space. Wall-clock-clamped durations and the flash governor
  (ADR-0011). `prefers-reduced-motion` support.
- Accept: scrubbing anywhere gives the same frame as playing to that point (golden test). The
  ×16 burst fixture never exceeds the flash threshold. Reduced motion disables streaks and bundles.

**B11 · Focus, evidence drawer & accessibility**
- Deliverables: click/keyboard focus on rows, cells and nodes. Stage highlight and ring snap.
  An evidence drawer (plain, readable DOM) for raw diff, samples and verdict text. Parallel
  accessible DOM (`role="log"` with throttled announcements, semantic tables, labelled canvases).
- Accept: every focus action is reachable by keyboard. axe-core reports no serious violations.
  A screen reader announces feed summaries at the throttled rate.

**B12 · UI quality gate**
- Deliverables: Playwright visual regression (pinned image, SwiftShader, CI-only baselines,
  `renderAt(t)` test hook), allocation test (`frame()` ×1000 with flat heap), size-limit
  (300 kB gzip) on every PR, and a nightly perf job with relative-regression gating.
- Accept: all gates run in CI and fail on an injected regression of each type.

**B13 · Visual parity** (added 2026-10-14, before Gate B)
- Why: the gate's side-by-side check against the reference frame at 6 s found that the ring and
  streams did not read as the same family. The panels and header did. The cause was the ring
  geometry taken from the reference's portrait frame.
- Deliverables: landscape ring geometry (the arc spans the stage height), larger and legible
  file cards, lanes within the visible arc, and an event-driven stream residue.
- Accept: the side-by-side reads as the same visual family. All B01–B12 tests and gates still
  pass, with the changed pins explained by the intended constants only.

**B14 · Reference parity II** (added 2026-10-14, at the owner's Gate B review)
- Why: the owner judged that the cockpit still did not look like the reference video and
  supplied a sharper copy (1080×1350, 60 fps). Measured against it, the web was a ball of
  spokes, the ring a thin band and the right of the stage nearly empty, on a 2.76:1 stage
  where the reference's 1.15:1 composition could not fit.
- Deliverables: a layout that keeps the stage near the reference's shape (panels beside the
  stage on landscape screens); the ring geometry measured on the reference; an opaque drum of
  full-band code cards built from events; a chord layout with arc edges and the run's nodes
  scattered across the visible web by a placement rule; bunches of dim strands, one from each
  node connected to what an event is about, with white bullets converging on its ring section,
  attached to their nodes and following that section as it turns (at its measured speed); lines
  outside the wheel only where a bunch hits, bouncing out from that section and back into the
  rim, turning with it; mesh arcs kept inside the wheel; dim lines MAX-blended below the flash
  threshold by construction (ADR-0011 note).
- Accept: the side-by-side reads as the same visual family (the owner confirms). All tests and
  gates pass; every changed pin is explained; the flash bound holds at every speed.

**B15 · Stage settings and the whole wheel** (added 2026-10-14, at the owner's seventh B14
review)
- Why: the owner wants users of the finished product to tune the stage's look themselves, to see
  how the simulation reads with its nodes spread wider, and to see the parts of the wheel that
  sit off the stage.
- Deliverables: a settings menu with sliders for the thickness and brightness of the strands,
  bullets, mesh arcs, nodes and lines outside the wheel, kept per viewer; nodes placed over the
  whole disc by the placement rule; a view control that shows the default arc or the whole
  wheel (and the parts between), with strands, hits and hit tests following the view.
- Accept: every slider changes only the look, never which events draw (render-model pins at the
  defaults unchanged); settings survive a reload and a reset restores the defaults; brightness
  of the uncounted dim lines stays at or under the flash bound at every setting, and the
  governor's bound holds at every width; the whole-wheel view shows every card and its bunches;
  the controls are keyboard-operable and labelled; all tests and gates pass.

---

## Stage C — Engine core & live stream · v0.3.0

**Goal:** a real engine that persists and streams events, analyses a real repo statically, and
drives the cockpit live.

**Gate C (passed 2026-10-14, v0.3.0, record: [`phases/C-gate.md`](phases/C-gate.md)):**
`repro run --repo <small OSS lib> --base A --head B` shows that repo's real import
graph and blast radius in the cockpit live, and `repro replay` reproduces the same UI state.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| C01 | Engine skeleton, configuration & CLI | S | A05 | done |
| C02 | Storage & evidence store | M | C01 | done |
| C03 | Event bus & orchestrator | M | C02 | done |
| C04 | API & WebSocket transport | M | C03 | done |
| C05 | Git layer: refs, diffs, worktrees, snapshots | M | C01 | done |
| C06 | Symbol table & changed-symbol mapping | M | C05 | done |
| C07 | Import graph & blast radius | M | C06 | done |
| C08 | Live UI wiring & end-to-end | M | B12, C04, C07 | done |

**C01** Typer CLI (`run`, `replay`, `serve`, `doctor`, `export`), settings (env plus
`~/.repro/config.toml`), structured logging, Windows-safe data paths. *Accept:* `repro doctor`
reports git, uv, Python and Docker capabilities on Windows and Linux.

**C02** SQLite schema and migrations (WAL, single batching writer, `BEGIN IMMEDIATE`), evidence
records, content-addressed artifact store, run registry. *Accept:* 100k events written and read
back in order. A crash mid-batch leaves a consistent DB (test). `UNIQUE(run_id, seq)` is enforced.

**C03** Stage DAG, job runner, cancellation, per-job timeouts, failure → `status: fail` events
plus missing items, guaranteed `run.finished`. *Accept:* golden-log test of a stub pipeline. A
cancelled run ends cleanly with a final event.

**C04** FastAPI REST and WebSocket with subscribe-before-replay, `caught_up` frame, batched
frames, per-client bounded queue and resync close, localhost binding. *Accept:* no gaps or
duplicates when a client connects mid-run (concurrency test). A slow client is dropped and
recovers with `?from=`.

**C05** Ref resolution, `-U0` diff parsing, worktrees with `-c core.longpaths=true` and robust
cleanup, `.git`-less snapshots via `git archive`. *Accept:* tests cover long paths, spaces,
CRLF repos, detached HEADs and locked-file cleanup on Windows.

**C06** `ast` symbol table, changed-lines → symbols on both sides, rename and move matching,
module-level changes, `symbol.changed` events. *Accept:* fixture repos with edits, renames,
moves, decorators and class-body changes map correctly (table-driven tests).

**C07** grimp import graph, downstream modules, blast radius with distance, `graph.*` and
`blast.computed` events. *Accept:* on fixture repos and one pinned real repo, the blast radius
matches hand-checked expectations.

**C08** UI WS client with gap detection and live/replay switch. `repro serve` serves the built
UI. End-to-end test on a real small repository. *Accept:* Gate C scenario passes in CI (Linux)
and manually on Windows.

---

## Stage D — Sandboxed two-sided execution · v0.4.0

**Goal:** run the right tests on both sides, honestly isolated, and turn outcomes, test edits
and timings into evidence.

**Gate D (passed 2026-10-15, v0.4.0, record: [`phases/D-gate.md`](phases/D-gate.md)):** a branch that breaks one test shows exactly that test flipping (P2F) in Execution,
Change routes and the index. A branch that deletes or weakens a test is flagged as test tampering.
The sandbox escape suite passes at the level the card claims.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| D01 | Sandbox abstraction & process isolation (L0–L2) | M | C03, A08 | done |
| D02 | Container backend (L3) | M | D01 | done |
| D03 | Environment provisioning | L | D02 | done |
| D04 | Repro pytest plugin v1 & deterministic run config | M | D03 | done |
| D05 | Test selection via per-test function map | M | D04, C07 | done |
| D06 | Two-sided execution, outcome & test-change diff, flake control | L | D05 | done |
| D07 | Performance pass | M | D06 | done |

**D01** Process runner, minimal environment, Windows Job Objects / POSIX process groups and
rlimits, timeouts, kill and cleanup, `sandbox.ready` with isolation level. Audit-hook
detection. *Accept:* fork-bomb, memory-hog and timeout fixtures are contained. Detected network
and subprocess attempts become evidence.

**D02** Docker backend: `--network none`, read-only root, `/tmp` tmpfs mounted **`exec`** (A08:
the default `noexec` breaks real suites), cap-drop, non-root, limits, snapshot volumes. Capability detection in `repro doctor`. *Accept:* escape suite (raw `_socket`,
`ctypes`, subprocess `curl`, writes outside the work dir, host `.git` tampering) is blocked at
L3. The card reports `network: enforced` only here.

**D03** Project-type detection (uv / poetry / pdm / requirements / setup.py), same interpreter on
both sides (≥ 3.12, explicit), provisioning in the sandbox with index-only egress or offline
wheels, `--link-mode copy`, env cache keyed by lock digest, `environment_failed` path. A08
measured offline `uv sync` from a warm cache at 0.79 s vs 9.9 s cold, so the design is a
cache-populating step followed by offline installs. A documented base image with common CLI
tools is needed, because missing tools (A08: `less`) cause base-and-head failures that must
surface as environment items.
*Accept:* five fixture project types provision on both OSes. A broken install gives
UNVERIFIABLE with a reason, never a crash.

**D04** Plugin skeleton (stdlib-only, Python 3.12–3.15), results channel via output directory,
fixed seeds, `-o addopts=` override, net guard plus audit hook, JUnit capture. *Accept:* the
plugin's own test matrix passes on 3.12, 3.13, 3.14 and 3.15. The same test run twice gives the
same outcomes.

**D05** collect-map mode (PY_START per test, DISABLE elsewhere), selection rule (ARCHITECTURE
§4.1), cache, `test.selected` with reasons, `no_covering_test` items. *Accept:* on fixtures,
selected tests ⊇ the tests a human expects. Uncovered changed symbols are always listed.

**D06** A, A′ and H runs. F2P / P2P / P2F / F2F / new / removed / modified classification.
Reruns for flips. DeFlaker check. Base version of modified tests run on head. Test-file diff
(removed tests, skip/xfail added, assertions removed or weakened). *Accept:* the `honest-fix`,
`flaky`, `untested` and new `test-tamper` fixtures produce exactly the expected evidence.

**D07** Untraced ABBA-interleaved timing runs, A/A noise floor, BCa bootstrap CI on the ratio of
medians, `perf_not_assessable` below the timing floor. *Accept:* `perf-regress` is flagged with
a CI excluding 1. An A/A run on an unchanged repo produces no perf diff over 20 repetitions.

---

## Stage E — Behaviour evidence · v0.5.0

**Goal:** function-level "same input, different output" evidence that is stable, explainable
and cheap enough to run.

**Gate E (passed 2026-10-15, v0.5.0, record: [`phases/E-gate.md`](phases/E-gate.md)):** a change that alters a function's output **without failing any test** is detected,
shown as a stable behaviour diff with a reproducer, and is not confused with noise in the A/A′
control.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| E01 | Tracer core & overhead benchmark | L | D04, A08 | done |
| E02 | Canonical value encoder | L | E01 | done |
| E03 | Observation store & stability analysis | M | E02 | done |
| E04 | Behaviour diff engine & reproducers | M | E03 | done |
| E05 | API & dependency diff | M | C06, D03 | done |
| E06 | Deterministic input-replay probes | M | E04 | done |
| E07 | Real-data panels & Gate E scenario | M | E04, E05 | done |

**E01** Tool-ID negotiation, PY_START / PY_RETURN / PY_UNWIND / yield capture, per-test
attribution, budgets and truncation, threads and generators, plus a benchmark harness tracked
over time. *Accept:* overhead on the benchmark suite is within the budget set in A08. Tool-ID
conflict with coverage.py is handled and becomes an explicit missing item when unresolvable.

**E02** Structural encoder (ARCHITECTURE §4.3). *Accept:* Hypothesis property tests (stable
across processes and hash seeds, never calls user dunders, guards terminate, truncated values are
never equal) plus a fuzz corpus of hostile objects.

**E03** Observation storage (CBOR artifacts plus index), join keys, A vs A′ unstable-field
masking. *Accept:* on fixtures with time, random and set-order noise, all noise is classified
`unstable` and none of it becomes a diff.

**E04** Diff classes (different result, new/changed exception, input mutation, order-only),
evidence with reproducers, `diff.behaviour` events. *Accept:* the `silent-change` and
`false-claim` fixtures produce the expected stable diffs with working reproducers.

**E05** griffe (`allow_inspection=False`) breaking changes plus annotation comparison. Lockfile
and installed-set dependency diff, lock drift. *Accept:* the `api-break` and `new-dep`
fixtures pass, and griffe never imports target code (test with a module that raises on import).

**E06** Hypothesis-generated JSON-able inputs for annotated pure functions, replayed in both
sandboxes. `probe_diff` evidence with `provenance: generated`. *Accept:* finds the seeded
divergence in a fixture whose existing tests don't exercise it. Probes are deterministic across
A and A′.

**E07** Behaviour diff field, Execution and Signals driven by real engine data. *Accept:* Gate E
scenario passes end to end in the cockpit.

---

## Stage F — Policy & deterministic card · v0.6.0

**Goal:** declared invariants and a complete, rule-based card. Repro is useful with no LLM at all.

**Gate F (passed 2026-10-15, v0.6.0, record: [`phases/F-gate.md`](phases/F-gate.md)):** each invariant has a passing and a failing fixture. Every fixture repo yields the
expected deterministic status. `repro replay` regenerates identical cards.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| F01 | `repro.yml` schema & loader | S | A04 | done |
| F02 | Invariants I: API, dependencies, forbidden paths | M | F01, E05 | done |
| F03 | Invariants II: network, performance | M | F01, D02, D07 | done |
| F04 | Status engine & missing taxonomy | M | F02, F03, E04 | done |
| F05 | Deterministic card & exports (MD/JSON) + card view | M | F04 | done |

**F01** JSON Schema for `repro.yml`, read from **base**, line-numbered errors, defaults, and a
head-side policy change becoming a gap item. *Accept:* invalid configs give actionable errors.
Policy weakening in head is flagged.

**F02 / F03** Each invariant emits `invariant.result` plus evidence. `no_network` distinguishes
enforced from detected-only. *Accept:* passing and failing fixture per invariant.

**F04** Status rules (ARCHITECTURE §4.6), introduced vs pre-existing origin, missing taxonomy
(§6.1), and an "explained-by" structure (filled by agents in G). *Accept:* property test
"never VERIFIED while any unexplained stable gap or failed invariant exists". Table-driven tests
over all rule combinations.

**F05** Card assembly, Markdown and canonical JSON export, card UI view, replay regeneration.
*Accept:* golden cards for every fixture. Byte-identical cards from replay.

---

## Stage G — Agents · v0.7.0

**Goal:** claims extracted from PR text, judged strictly against cited evidence. Generated
probes close the test-adequacy gap.

**Gate G (passed 2026-10-17, v0.7.0, record: [`phases/G-gate.md`](phases/G-gate.md)):** on three
handmade PRs (honest; unclaimed behaviour change; false claim) the cards come out VERIFIED /
FLAGGED / FLAGGED with correct cited evidence, reproducibly from cassettes. The agent eval set
meets the thresholds set in G09.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| G01 | LLM backend & locked-down runtime | M | C03 | done |
| G02 | Cassettes, prompt versioning & cost accounting | M | G01 | done |
| G03 | Read-only evidence tools | M | G01, F04 | done |
| G04 | Reader agent (claim extraction) | M | G02, G03, G10 | done |
| G05 | Reviewer agents (evidence-constrained verdicts) | L | G04 | done |
| G06 | Gap reviewer (explained-by mapping) | M | G05 | done |
| G07 | Probe-writer agent | L | G03, E06 | done |
| G08 | Bias controls & QA agent | M | G05, G06 | done |
| G09 | Agent evaluation harness | M | G04–G08 | done |
| G10 | OpenAI-compatible backend (Groq, local models) | M | G01, G02 | done |

**G01** `LLMBackend` protocol, Agent SDK implementation with the full lockdown (ADR-0007),
budgets, concurrency limiter, `agent.spawned` and `agent.state` events. *Accept:* tests prove
built-in tools are unavailable, a `CLAUDE.md` or hook planted in the target repo is never loaded,
and calls outside `mcp__repro__*` are denied.

**G02** Cassettes keyed by (prompt version, model, input digest), with a missing cassette failing
the test. Prompt files versioned. `cost.estimate` events. *Accept:* the full agent test suite
runs offline in CI.

**G03** `list_evidence`, `get_evidence`, `read_snippet`, `get_diff` with size caps, rendering, and
path normalisation. Untrusted-content delimiting. *Accept:* prompt-injection fixtures in PR text
and code comments do not change tool behaviour or verdicts.

**G04** Atomic claim extraction (text, targets, kind) with schema validation. *Accept:*
precision and recall on the labelled claim set are recorded. Thresholds are set and met.

**G05** Per-claim schema with an evidence-ID enum, quote substring validation,
consistency-with-evidence check, `verdict.rejected` path. *Accept:* zero uncited verdicts
reach the card (property test with adversarial cassettes).

**G06** Explained-by mapping for every gap item, citing evidence. Unexplained items flag.
*Accept:* the `silent-change` fixture with a claim that does not cover it is flagged as
unexplained.

**G07** Probe writer (ARCHITECTURE §4.4): claim-blind, TestGen-LLM-style filters, run on both
sides. *Accept:* on a fixture whose tests pass on a wrong change, generated probes expose the
divergence. The filter rejection rate is reported.

**G08** Order-swap pass for contradicted verdicts, optional 2-of-3 panel, judge-model ≠
author-model policy, QA agent. *Accept:* verdict flip rate under order swap is measured and
reported on the eval set.

**G09** Labelled mini-set of handmade PRs. Metrics: claim P/R, verdict accuracy, citation
validity, flip rate, cost. Regression gate for prompt or model changes. *Accept:* metrics are
appended to `bench/results/agent-evals.csv`. CI fails on a regression beyond tolerance.

**G10** *(added 2026-10-16, ADR-0016; done before G04)* A second `LLMBackend` for
OpenAI-compatible Chat Completions APIs (Groq, local servers, routers): Repro's own tool loop,
schema-checked structured answers, rate limits honoured, costs from sourced prices. Stage G's
recordings and evaluation use Groq's `openai/gpt-oss-120b` (the owner's key); the Claude Agent
SDK stays the default. *Accept:* the tool loop refuses every tool outside the call's own;
budgets end calls with the right outcome; a call records and replays through a cassette; one
live call succeeds.

---

## Stage H — Hardening & first release · v1.0.0

**Goal:** a stranger can install Repro, run it on their own repo, and trust what the card says
about what was and wasn't checked.

**Gate H (passed 2026-10-17, v1.0.0, record: [`phases/H-gate.md`](phases/H-gate.md)):**
install-from-scratch tests pass on Windows and Linux. The security review is complete. The user
documentation is complete. `v1.0.0` is tagged and published.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| H01 | Robustness & failure modes | M | G09 | done |
| H02 | Performance & scale | M | H01 | done |
| H03 | Security hardening | M | H01 | done |
| H04 | Exports: in-toto, JUnit, SARIF | M | F05 | done |
| H05 | Packaging & distribution | M | H02, H03 | done |
| H06 | User documentation & demo | M | H05 | done |

**H01** Failure catalogue (install failure, collection error, timeout, OOM, encoder failure, API
outage) mapped to explicit missing/UNVERIFIABLE outcomes. Chaos tests. *Accept:* every catalogued
failure has a test and a user-readable reason.

**H02** Profiling on 3 pinned real repos of increasing size. Caching (env, selection map,
graph), parallel sides, incremental re-runs. *Accept:* targets set from A08 and D-stage
measurements are met and recorded.

**H03** Threat-model review, expanded escape suite, gVisor on Linux CI, secret-scrubbing audit
of logs and events, dependency audit. *Accept:* every THREAT_MODEL item has a test or a
documented residual risk.

**H04** in-toto Statement with the Repro predicate (ADR-0012), JUnit (one testcase per claim),
SARIF 2.1.0. Predicate schema published. *Accept:* outputs validate against their specs and
schemas. Card → attestation → card round trip.

**H05** Package name decision (PyPI/npm availability), `uvx`/pipx install, web assets bundled
into the Python package, release workflow. *Accept:* clean-machine install and first run on
Windows and Linux in under 10 minutes (documented procedure plus CI job).

**H06** Quickstart, `repro.yml` reference, "how to read a card", troubleshooting, architecture
overview for contributors, recorded demo. *Accept:* a reviewer unfamiliar with the project
completes the quickstart from the docs alone.

---

## Stage I — GitHub integration · v1.1.0

**Gate I (passed 2026-10-18, v1.1.0, record: [`phases/I-gate.md`](phases/I-gate.md)):** on a
test repository, opening a PR produces a Repro check run, a sticky card comment and (on public
repos) a signed attestation, and re-running updates rather than duplicates them.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| I01 | PR ingestion | M | H05 | done |
| I02 | Check run, sticky comment & job summary | M | I01 | done |
| I03 | GitHub Action & attestation signing | M | I02, H04 | done |
| I04 | GitHub App (optional) | L | I03 | done ([#117](https://github.com/DuhItzAniket/Repro/pull/117)), at the owner's request (2026-10-21) |

**I01** PR URL → refs, description, commits (gh/API, token from env), fork handling.
**I02** Conclusion mapping (VERIFIED→success, FLAGGED→failure or action_required, UNVERIFIABLE→
neutral), annotations in batches of 50, sticky comment found by a hidden marker, under 60k
characters, job summary. **I03** Docker-based Action for Linux runners, caching,
`actions/attest` with the Repro predicate. **I04** Webhooks, re-run button, org config. Built
only if users ask for it. **Done (I04, ADR-0019):** built on the owner's request; `repro app
register` and `serve`, on the operator's machine through a relay or a port of its own.

---

## Stage J — Evaluation & research · v1.2.0

**Goal:** a reproducible, pre-registered evaluation that supports or refutes Repro's claims, and
a paper. Protocol: [`paper/EVALUATION_PROTOCOL.md`](paper/EVALUATION_PROTOCOL.md) and ADR-0010.

**Gate J:** every table and figure regenerates from one command over pinned manifests. The
artifact is archived with a DOI. The paper is submitted.

**Deferred by the owner (2026-10-19):** the study (J06 to J10) waits for a budget and two
annotators. Its harness (J01 to J05) and what J06, J09 and J10 prepared shipped in v1.2.0;
Gate J and the study's release come later.

| ID | Phase | Size | Depends | Status |
|---|---|---|---|---|
| J01 | Benchmark infrastructure & input contract | L | H05 | done |
| J02 | Ground-truth pipeline & annotation tooling | L | J01 | done |
| J03 | Baselines | L | J01 | done |
| J04 | S0 dev set & S1 pilot | M | J02, J03 | done |
| J05 | S2 patch generation on fresh tasks | M | J04 | done |
| J06 | Annotation, κ & label freeze; pre-registration | L | J05 | deferred (owner, 2026-10-19): prepared; budget, then labels |
| J07 | S2 main study | M | J06 | deferred (owner, 2026-10-19) |
| J08 | Ablations, S3 in-the-wild & S4 synthetic | M | J07 | deferred (owner, 2026-10-19) |
| J09 | Artifact package | M | J08 | deferred (owner, 2026-10-19): prepared (kick-the-tires, AE guide, metadata); results and DOI wait |
| J10 | Paper writing & submission | L | J08 | deferred (owner, 2026-10-19): draft 0 prepared; results, review and submission wait |

**J01** SWE-format adapter (instance + image + patch → base/head), image management by digest,
manifest format, input-contract enforcement with a test proving withheld fields never reach the
engine. **J02** Tier A (hidden + full suite) and tier B (UTBoost augmented tests,
PatchDiff-style differential tests vs gold) labelling, annotation guideline and tool, κ
computation. **J03** B0 CI-only, B1 diff-only LLM, B2 agentic static LLM, B3 PR-Agent (licence
re-checked at the pinned commit), Testora where reproducible. Pre-registered prompts and
mapping rules. **J04** Pipeline shake-out (S0, never reported), S1 pilot, updated power
analysis, memorisation probe. **J05** Agent patches from at least 2 scaffolds × at least 2 model
families on post-cutoff SWE-rebench / SWE-bench-Live tasks. **J06** Two blinded annotators,
adjudication, labels frozen and tagged with the analysis config. **J07** All systems, k = 5;
McNemar + Holm; cluster bootstrap; cost and time. **J08** Ablations
(−traces, −probes, −selection, −invariants, −agents, −claims, −citation rule, model swap),
S3 and S4. **J09** Zenodo DOI, image digests, 30-minute kick-the-tires subset, AE-ready README.
**J10** Paper drafting from `docs/paper/`, internal review, submission.

---

## Stage K — Expansion

K01 to K05 shipped in v1.2.0. The owner accepted ADR-0017 and ADR-0018 on 2026-10-19:
their remaining parts are K06 to K10. On 2026-10-20 the owner split ADR-0017's phase 4 in two:
the card first (K08), then the container and the compiler's view (K11).

| ID | Phase | Notes |
|---|---|---|
| K01 | TypeScript/JavaScript targets | Vitest/Jest. Needs a V8-side tracer design. **Done (K01, [#88](https://github.com/DuhItzAniket/Repro/pull/88)):** outcomes built (`repro js-run`); tracer and card proposed in ADR-0017 |
| K02 | Fleet dashboard | Many PRs and agents at once (the "64 papers" view). **Done (K02, [#89](https://github.com/DuhItzAniket/Repro/pull/89)):** `/fleet.html` |
| K03 | Repro as an MCP server | Agents can request verification and receive evidence, while the verifier stays independent. **Done (K03, [#90](https://github.com/DuhItzAniket/Repro/pull/90)):** `repro mcp` |
| K04 | Symbolic deep checks | CrossHair `diffbehavior` on small pure functions in the blast radius. **Done (K04, [#91](https://github.com/DuhItzAniket/Repro/pull/91)):** `repro symbolic` |
| K05 | Remote sandbox backends | Self-hosted E2B / Firecracker for teams. **Done (K05, [#92](https://github.com/DuhItzAniket/Repro/pull/92)):** `docker_context`; E2B and Firecracker designed (ADR-0018, proposed) |
| K06 | JavaScript: the test → function map | ADR-0017 phase 2: per-test V8 precise coverage from a setup file, the map selection (D06) needs. **Done (K06, [#100](https://github.com/DuhItzAniket/Repro/pull/100)):** `repro js-run` reports the tests a change reaches |
| K07 | JavaScript: function-level traces | ADR-0017 phase 3: a `load` hook wraps the changed functions with an encoder ported from `repro_encode`. **Done (K07, [#105](https://github.com/DuhItzAniket/Repro/pull/105)):** each runner's transform point instead of a `load` hook; base twice and head compared as E03 does |
| K08 | JavaScript: the card | ADR-0017 phase 4, first part: the target's language in the event schema, and `repro run` makes a JavaScript or TypeScript change's card (cockpit, exports, review), at L2. **Done (K08, [#107](https://github.com/DuhItzAniket/Repro/pull/107)):** the same events as Python's; what is not checked yet is a missing item each |
| K09 | E2B self-hosted sandbox | ADR-0018 option B: `E2BSandbox`, a microVM per side, isolation level L5 in the schema. Real tests need a Linux host with KVM: K10 found WSL2 and GitHub's runners to be such hosts. **Done (K09, [#119](https://github.com/DuhItzAniket/Repro/pull/119)):** `sandbox = "e2b"` on E2B Embed; each side's network closed by the VM's own kernel firewall and checked from inside, since E2B's own let TCP to numbered addresses through (measured); the target as a user without sudo; CI job `e2b` |
| K10 | Kata Containers with Firecracker | ADR-0018 option C: Repro's containers under the `kata-fc` runtime class on a containerd host. Needs a Linux host with KVM. **Done (K10, [#116](https://github.com/DuhItzAniket/Repro/pull/116)):** with Cloud Hypervisor in place of Firecracker, by the owner's choice (Firecracker's storage is block devices only, so Repro's volumes cannot reach it), under Docker: `docker_runtime = "kata"`, L5 when a probe sees a kernel of the container's own; the process limit at L5 is uid 1000's `nproc`, as Kata's VM applies no pids limit; `scripts/kata/setup.sh` for a host (WSL2 included); CI job `kata` |
| K11 | JavaScript: L3 and the compiler's view | ADR-0017 phase 4, second part: L3 in a container with Node, the public API diff and edited tests' assertions from the TypeScript compiler API, and base's version of an edited test run against head. **Done (K11, [#110](https://github.com/DuhItzAniket/Repro/pull/110)):** the Node image and its own install container; pnpm's `.pnpmfile.cjs` and Yarn's `yarn-path` found running under `--ignore-scripts`, and turned off; a pinned TypeScript 6.0.3 for the API and assertions; base on head |

---

## Mapping from the original milestones

| Original | Now |
|---|---|
| M0 Scaffold | Stage A |
| M1 Cockpit on fixtures | Stage B |
| M2 Engine core | Stage C |
| M3 Run both sides | Stage D |
| M4 Behaviour traces | Stage E |
| M5 Invariants | Stage F |
| M6 Agents + card | Stage F (deterministic card) + Stage G |
| M7 GitHub | Stage I (Docker isolation moved earlier, to D02) |
| M8 Benchmark | Stage J (redesigned, ADR-0010) |

## Phase log

Completed phases append a one-line entry here. Detail lives in `docs/phases/`.

| Phase | Date | PR | Notes |
|---|---|---|---|
| A01 | 2026-10-13 | (bootstrap commit) | Planning baseline published |
| A02 | 2026-10-13 | [#1](https://github.com/DuhItzAniket/Repro/pull/1) | pnpm + uv workspaces, pinned toolchains, CI on Ubuntu + Windows; ADRs 0003/0004/0007/0011 accepted; Apache-2.0 |
| A03 | 2026-10-13 | [#3](https://github.com/DuhItzAniket/Repro/pull/3) | Ruff + strict mypy (ADR-0013), Biome, required `lint` CI job |
| A04 | 2026-10-13 | [#4](https://github.com/DuhItzAniket/Repro/pull/4) | Event schema v1: 34 kinds, models, examples; `repro-events` registry, validation, draft-07 agent schemas |
| A05 | 2026-10-13 | [#5](https://github.com/DuhItzAniket/Repro/pull/5) | In-house TS + Pydantic codegen (ADR-0014), three-validator equivalence tests, CI drift gate |
| A06 | 2026-10-13 | [#6](https://github.com/DuhItzAniket/Repro/pull/6) | CI: lint / test×2 / security (gitleaks, licence allow-list) / dependency review; SHA pins; Dependabot |
| A07 | 2026-10-13 | [#7](https://github.com/DuhItzAniket/Repro/pull/7) | Log validator/normaliser CLI, golden logs, Hypothesis/fast-check, deterministic fixture repos |
| A08 | 2026-10-13 | [#8](https://github.com/DuhItzAniket/Repro/pull/8) | Measured: tracer overhead (budget 200 → ~baseline), Docker L3 on Windows, offline provisioning 0.79 s |
| Gate A | 2026-10-13 | [#9](https://github.com/DuhItzAniket/Repro/pull/9) | Stage A complete; release v0.1.0 ([gate record](phases/A-gate.md)) |
| B01 | 2026-10-13 | [#10](https://github.com/DuhItzAniket/Repro/pull/10) | `@repro/synth` generator; demo (2,334 events, 28 checks) and stress (10k edges, 3k/s burst) fixtures; ADR-0015 check model |
| B02 | 2026-10-13 | [#11](https://github.com/DuhItzAniket/Repro/pull/11) | Columnar EventStore, snapshot folder with exact seek, time/kind indexes, presentation clock; run/summary/checks/claims/agents projections |
| B03 | 2026-10-13 | [#12](https://github.com/DuhItzAniket/Repro/pull/12) | Preact + signals shell, `computeLayout` geometry, header counters and 7-segment progress, tokens, self-hosted JetBrains Mono with OFL |
| B04 | 2026-10-13 | [#13](https://github.com/DuhItzAniket/Repro/pull/13) | WebGL `StageHost` + pure layers, segment primitive, render-model hashing, adaptive DPR, perf HUD; stress bench on integrated GPU: 60 fps at DPR 2 |
| B05 | 2026-10-13 | [#14](https://github.com/DuhItzAniket/Repro/pull/14) | Dependency web: worker d3-force layout (cached, equalised), shader-revealed edges, blast markers, Bézier bundles; 10k edges at 60 fps on an integrated GPU |
| B06 | 2026-10-14 | [#15](https://github.com/DuhItzAniket/Repro/pull/15) | Ring of file cards built from events, worker-drawn atlas, one instanced draw call, closed-form rotation with 500 ms snaps, shader-rotated ticks |
| B07 | 2026-10-14 | [#16](https://github.com/DuhItzAniket/Repro/pull/16) | Event streams on per-file lanes (≤ 2,048 live, velocity-stretched tails, DESIGN tints) and callouts; complete stage holds the budget at 2k streaks |
| B08 | 2026-10-14 | [#19](https://github.com/DuhItzAniket/Repro/pull/19) | Canvas 2D panels (feed, execution with A′, agent workspace, behaviour field) behind a version-gated `PanelHost`; golden draw-call recordings; paused = 0 redraws |
| B09 | 2026-10-14 | [#20](https://github.com/DuhItzAniket/Repro/pull/20) | Change routes, signals, dispatch and change×check index; `ChangeIndex` routes pure in the cursor; index, footer and header count one check list |
| B10 | 2026-10-14 | [#21](https://github.com/DuhItzAniket/Repro/pull/21) | Transport and Space pause; wall-clock motion; flash governor under half of WCAG 2.3.1 at every speed; reduced motion; scrub = play for stage and panels |
| B11 | 2026-10-14 | [#22](https://github.com/DuhItzAniket/Repro/pull/22) | Focus from click or keyboard (stage highlight, ring pin, panel outlines), evidence drawer, accessible parallel DOM; axe-core 0 violations; text contrast to WCAG AA |
| B12 | 2026-10-14 | [#23](https://github.com/DuhItzAniket/Repro/pull/23) | Visual regression (SwiftShader, CI baselines), flat-heap allocation, size-limit 300 kB, calibrated perf gate (nightly); each gate rejects an injected regression in CI |
| B13 | 2026-10-14 | [#24](https://github.com/DuhItzAniket/Repro/pull/24) | Visual parity found missing at the gate check: landscape ring geometry, 64 legible cards, lanes in the visible arc, shader-faded stream residue |
| Gate B | 2026-10-14 | [#25](https://github.com/DuhItzAniket/Repro/pull/25) | Stage B complete; release v0.2.0 ([gate record](phases/B-gate.md)); mid-laptop 60 fps check owed by the owner. Re-opened the same day at the owner's review (B14) |
| B14 | 2026-10-14 | [#26](https://github.com/DuhItzAniket/Repro/pull/26) | Reference parity II: panels beside a near-square stage, measured ring geometry, a drum of event-built code cards, a chord-layout web of arcs, bunches of strands landing on sections, lines outside that bounce from the hit section |
| B15 | 2026-10-14 | [#27](https://github.com/DuhItzAniket/Repro/pull/27) | Stage settings (thickness and brightness per part, kept per viewer, inside the flash bound), a zoom from the arc to the whole wheel, the run's nodes over the whole disc, callouts kept inside the stage |
| Gate B (closed) | 2026-10-14 | [#28](https://github.com/DuhItzAniket/Repro/pull/28) | The owner approved the look after B14 and B15; gate record re-measured; release v0.2.0 ([gate record](phases/B-gate.md)) |
| C01 | 2026-10-14 | [#29](https://github.com/DuhItzAniket/Repro/pull/29) | `repro doctor` (Python, git, uv, Docker, data paths, Windows long paths), layered settings with source-named errors, platform data paths, JSON logs on stderr; the other commands declared |
| C02 | 2026-10-14 | [#30](https://github.com/DuhItzAniket/Repro/pull/30) | SQLite store whose schema enforces append-only events and gap-free `seq`; a single batching writer that publishes only after commit; evidence tied to its event; atomic content-addressed artifacts; 100k-event round trip and kill-anytime crash tests |
| C03 | 2026-10-14 | [#31](https://github.com/DuhItzAniket/Repro/pull/31) | Event bus (envelopes, credential scrubbing, per-kind validation, publish after commit) and orchestrator (job DAG, concurrency, timeouts, cancellation with tool-tree kill, failures as `fail` events and missing items, guaranteed `run.finished`); golden stub-pipeline log |
| C04 | 2026-10-14 | [#32](https://github.com/DuhItzAniket/Repro/pull/32) | Read-only REST and a replay-then-live WebSocket (subscribe first, dedupe, `caught_up`, batched frames, resync on overflow, lossless `?from=`); local `Host` and allow-listed `Origin` (THREAT_MODEL T16); `repro serve` on loopback |
| C05 | 2026-10-14 | [#33](https://github.com/DuhItzAniket/Repro/pull/33) | Hardened git (no repo hooks or fsmonitor), refs and merge bases, exact-path diffs with line ranges, byte-for-byte snapshots from tree objects (not `git archive`) with unsafe entries skipped, worktrees cleaned up past Windows locks |
| C06 | 2026-10-14 | [#34](https://github.com/DuhItzAniket/Repro/pull/34) | Module names from packages, `ast` symbol tables with stable IDs, and changed symbols by own code (comments and reformatting are not changes); repeated names aligned, renames and moves by AST similarity, unreadable files reported, never guessed |
| C07 | 2026-10-14 | [#35](https://github.com/DuhItzAniket/Repro/pull/35) | Import graphs (grimp for packages in an isolated process that never resolves target names through `sys.path`, THREAT_MODEL T19; `ast` for tests and scripts outside packages) and the blast radius over both sides' imports, with distance; hand-checked on this repository's own history |
| C08 | 2026-10-14 | [#36](https://github.com/DuhItzAniket/Repro/pull/36) | `repro run` streams a change's build stage (snapshots, static analysis) into the cockpit live; `repro replay` and `repro serve` serve the built cockpit; the cockpit's event-stream client recovers gaps and resyncs from the engine's store; Gate C's Playwright test on pallets/itsdangerous in CI and on Windows (Edge) |
| Gate C | 2026-10-14 | [#37](https://github.com/DuhItzAniket/Repro/pull/37) | Stage C complete; release v0.3.0 ([gate record](phases/C-gate.md)); Gate C's end-to-end test passes in CI (Linux) and on Windows (Edge) |
| D01 | 2026-10-14 | [#38](https://github.com/DuhItzAniket/Repro/pull/38) | The process sandbox, L0 to L2: minimal environment; audit-hook detection of network, subprocess and file-write attempts as evidence; Job Objects (created suspended) on Windows, a session and resource limits on POSIX; bounded fork-bomb, memory-hog and timeout fixtures contained on both |
| D02 | 2026-10-14 | [#39](https://github.com/DuhItzAniket/Repro/pull/39) | The container sandbox, L3: the commit streamed from its tree object into a volume; hardened containers with no network, read-only root, exec tmpfs, no capabilities, uid 1000, limits; the escape suite blocked; `repro doctor` reports the isolation level runs reach |
| D03 | 2026-10-14 | [#40](https://github.com/DuhItzAniket/Repro/pull/40) | Provisioning: project kind and requirements read from files (index only); dependencies from wheels in a cached environment no target code has touched, copied per side; the project built offline in the sandbox; five fixture kinds on the host and in containers; uv cannot use a read-only cache (ADR-0004 note) |
| D04 | 2026-10-15 | [#41](https://github.com/DuhItzAniket/Repro/pull/41) | Repro's pytest plugin and command: per-test outcomes to a results file read as untrusted input, per-test random seeds, a loopback-only network guard recorded by the audit hook, pinned TZ and locale, collection errors no longer hide other tests; passes on Python 3.12–3.15 and in a container, under pytest 9.1.1 and 8.3.3 |
| D05 | 2026-10-15 | [#42](https://github.com/DuhItzAniket/Repro/pull/42) | Test selection: per-test function map from `sys.monitoring` (places relative to the snapshot, resolved to C06 symbols); tests selected by what ran on base, module-level changes over both import graphs, and added or edited tests, each with reasons; changed symbols no selected test runs become `no_covering_test`; base maps cached; same map on host and in a container; fixtures `selection` and `untested` |
| D06 | 2026-10-15 | [#43](https://github.com/DuhItzAniket/Repro/pull/43) | Two-sided execution in `repro run`: sandboxes and environments per side, A / A2 (reverse order, other hash seed) / H on the selected tests, k = 3 reruns of flips, edited tests' base versions on head; outcome classes and test-change evidence (removed tests, new skips and xfails, weaker assertions, widened raises); flaky tests and unfinished runs as missing items; an unbuildable environment is unverifiable, not a failed run; fixtures `flaky` and `test-tamper` |
| D07 | 2026-10-15 | [#44](https://github.com/DuhItzAniket/Repro/pull/44) | Performance pass: the tests passing on both sides timed untraced in ABBA blocks (10 runs a side, call phase); ratio of medians with a BCa 95 % interval, Mann-Whitney with Benjamini-Hochberg, noise floor max(10 %, A/A spread from base's own samples), 5 ms timing floor and `perf_not_assessable`; fixture `perf-regress`; statistics checked against SciPy and statsmodels |
| Gate D | 2026-10-15 | [#45](https://github.com/DuhItzAniket/Repro/pull/45) | Stage D complete; release v0.4.0 ([gate record](phases/D-gate.md)); Gate D's browser test (a test flipping P2F in Execution, Change routes and the index; test tampering flagged) passes in CI (Linux) and on Windows (Edge, containers) |
| E01 | 2026-10-15 | [#46](https://github.com/DuhItzAniket/Repro/pull/46) | Tracer core: every call of a change's target functions per test (arguments at PY_START, results, exceptions, yields) as stable digests; one sys.monitoring tool for map and tracer; 200 calls per test and target, then switched off completely; beside coverage.py; benchmark harness with BCa intervals, no interval above 1.20x on 3.12–3.15 |
| E02 | 2026-10-15 | [#47](https://github.com/DuhItzAniket/Repro/pull/47) | Canonical encoder: objects, builtin subclasses, exceptions and standard-library values read through the interpreter's own descriptors (no target code runs); paths and addresses normalised; guards; previews; for results an order-insensitive digest from the same walk; Hypothesis properties and an 18-kind hostile corpus; tracer benchmark 1.01–1.18x on 3.12–3.15 |
| E03 | 2026-10-15 | [#48](https://github.com/DuhItzAniket/Repro/pull/48) | Observations: A, A2 (now with its own random seed) and H trace the changed functions on both sides; calls joined by test, phase, symbol, arguments and occurrence; A vs A2 differences masked per call and field and per call set; incomplete or budget-cut calls never compared; deterministic CBOR artifacts (RFC 8949) read back strictly; noisy fixture: all noise unstable, 0 differences |
| E04 | 2026-10-15 | [#49](https://github.com/DuhItzAniket/Repro/pull/49) | Behaviour differences as evidence: classified (result, exception added/removed/changed, order only, input mutated, calls changed), grouped per test, function and class, run again on both sides for a reproducer with previews, behaviour_diff evidence and a warn diff.behaviour each; tracer encodes mutable arguments again at the end of a call; silent-change and false-claim fixtures give stable, reproduced diffs |
| E05 | 2026-10-15 | [#50](https://github.com/DuhItzAniket/Repro/pull/50) | API and dependency differences: griffe (griffelib 2.3.2, allow_inspection=False, files from git) breaking changes plus Repro's return/attribute annotation comparison as api_diff + diff.api; requirements (lock or declared) and installed sets listed in each sandbox with -I -S as dep_diff + diff.deps; lock drift; api-break and new-dep fixtures; a trap module proves griffe imports nothing |
| E06 | 2026-10-15 | [#51](https://github.com/DuhItzAniket/Repro/pull/51) | Input-replay probes: changed functions with JSON-typed parameters called with 40 derandomized Hypothesis inputs each on base, base again (other seeds) and head inside the sandboxes; inputs on which base disagrees with itself left out; probe_diff (provenance: generated) + diff.probe; probe-only fixture: percent(0, 0) raises on head only; timing now runs after the other runs |
| E07 | 2026-10-15 | [#52](https://github.com/DuhItzAniket/Repro/pull/52) | The behaviour field and Signals from real runs: A, A2 and H preview each test's first four calls of each traced function, sent as trace.sample events; Gate E's browser test (silent-change: two stable behaviour diffs with reproducers 37.8 -> 38 and 0.0 -> 0, no failing test; noisy: noise unstable, no diff) and its CI job |
| Gate E | 2026-10-15 | [#53](https://github.com/DuhItzAniket/Repro/pull/53) | Stage E complete; release v0.5.0 ([gate record](phases/E-gate.md)); Gate E's browser test (an output change no test checks shown as two stable behaviour diffs with reproducers; A/A′ noise unstable, never a diff) passes in CI (Linux, containers) and on Windows (Edge) |
| F01 | 2026-10-15 | [#54](https://github.com/DuhItzAniket/Repro/pull/54) | repro.yml read from base and validated against a JSON Schema (PRODUCT.md's MVP set) with PyYAML's safe loader, bounded, no aliases, each error with line, column and path; an invalid file means no policy, never defaults; head's edit is policy_change evidence naming how it would weaken the policy; policy-weaken fixture |
| F02 | 2026-10-15 | [#55](https://github.com/DuhItzAniket/Repro/pull/55) | public_api_stable, no_new_dependencies and forbidden_paths evaluated from the run's evidence: invariant evidence citing what it rests on and an invariant.result each, not evaluated (never passed) when that evidence is missing; Job.after so a failed job leaves the invariants not evaluated instead of skipped; declared metadata compared beside locks and requirement files; directory patterns, and patterns that cannot match refused; forbidden-path and invariants-pass fixtures |
| F03 | 2026-10-15 | [#56](https://github.com/DuhItzAniket/Repro/pull/56) | no_network fails on head's network attempts past this machine (loopback and Unix sockets are local), citing the sandbox's evidence, and its pass states enforced (L3) or detected-only, with a network_not_enforced item for the latter; perf_budget fails a D07 regression whose interval is past max_regression, and is not evaluated when the slowdown is within this machine's noise; net-call fixture (TEST-NET-1), perf-regress and invariants-pass enable them |
| F04 | 2026-10-15 | [#57](https://github.com/DuhItzAniket/Repro/pull/57) | VERIFIED, FLAGGED or UNVERIFIABLE by deterministic rules over a run's event log, each reason citing evidence or targets: a difference no supported claim explains flags (all of them without a review stage), anything not checked leaves the change unverifiable, including changed symbols masked as noise; policy evidence on every run so a replay decides the same; property test and a table of every rule; every fixture's status asserted from its log |
| F05 | 2026-10-15 | [#58](https://github.com/DuhItzAniket/Repro/pull/58) | every run ends with its Reproduction Card, assembled from the run's own log (F04 status and reasons, conditions, gap with resolutions, invariants, missing items, replay digest), published in card.published with the card and stored; repro export (md, json) refuses a card that differs from its log and repro replay reports it; the cockpit's header button opens the card in the drawer; golden cards for every fixture, byte-identical when assembled again |
| Gate F | 2026-10-15 | [#59](https://github.com/DuhItzAniket/Repro/pull/59) | Stage F complete; release v0.6.0 ([gate record](phases/F-gate.md)); each of the five invariants passes on one fixture and fails on another, all 17 fixtures get their expected deterministic status from their own logs, and every card assembled again from its stored log is byte-identical, in CI on Ubuntu and Windows |
| G01 | 2026-10-15 | [#60](https://github.com/DuhItzAniket/Repro/pull/60) | LLMBackend protocol with outcomes instead of exceptions; Claude Agent SDK 0.2.167 under ADR-0007's lockdown (no built-in tools, a gate permitting only the call's own mcp__repro__ tools and StructuredOutput, no settings, CLAUDE.md, memory, plugins, skills or other MCP servers, empty per-call cwd and config dir, turn/dollar/time budgets); tested against the bundled CLI with a local Messages API stand-in: planted CLAUDE.md, hooks and more load on an unlocked CLI and never under the lockdown, foreign tools denied with no effect; dispatch min(12, free GiB) in 12 slots with agent.spawned/agent.state/cost.estimate |
| G02 | 2026-10-16 | [#61](https://github.com/DuhItzAniket/Repro/pull/61) | Cassettes keyed by prompt version, model and an input digest taken with run-specific IDs (ev_, evt_, run_) normalised by first-seen order, replayed in any later run with that run's IDs; recorded tool calls made again (stale if they differ); a missing cassette raises and replay has no backend to call; canonical scrubbed JSON; versioned prompt files with a SHA-256 lock; per-model cost.estimate with source live or cassette; RunLedger run budget; every test offline (closed loopback API, CLI tests fail on any connection past loopback) |
| G03 | 2026-10-16 | [#62](https://github.com/DuhItzAniket/Repro/pull/62) | list_evidence, get_evidence, read_snippet and get_diff over the run's own evidence and the commits' git objects (never the snapshots), by checked paths, with size caps; evidence rendered the same in every run and on every machine (content order, per-type fields, no timings, pids or digests, portable paths, ~ for home); the target's text in delimited blocks it cannot close; injection fixture: tool outputs and card equal honest-fix's |
| G10 | 2026-10-16 | [#63](https://github.com/DuhItzAniket/Repro/pull/63) | Added by the owner's decision (ADR-0016): a second LLMBackend for OpenAI-compatible APIs (Groq, local servers) with Repro's own tool loop (only the call's tools, anything else refused), structured answers asked for alone and schema-checked, retry-after honoured, the key kept out of results, sourced prices (unpriced: no cost.estimate); one live gpt-oss-120b call with a tool and a strict schema: ok, 591+121 tokens, an estimated $0.000161 |
| G04 | 2026-10-16 | [#64](https://github.com/DuhItzAniket/Repro/pull/64) | Agent settings (off by default; claude, groq, openai; live, record, replay; a 2.0 USD run budget); the reader (reader/1) reads the PR's title and description or commit messages, delimited and capped, into checked, numbered claim.extracted events tied to changed files; a failed reader is agent_failed; labelled set of 18 PR texts and 43 claims: precision 0.955, recall 0.977 live on Groq gpt-oss-120b, replayed in CI from 18 cassettes against proposed 0.90 thresholds |
| G05 | 2026-10-16 | [#65](https://github.com/DuhItzAniket/Repro/pull/65) | One reviewer per claim (reviewer/1) given the claim, the other claims, the claim's evidence in full and its files' diffs; a per-claim schema with an enum of evidence IDs and minItems 1; validation of citations, exact quotes and consistency (no-change supported by a change only where another claim describes it); one retry, then verdict.rejected; 300 adversarial answers pass none; live on Groq and replayed in CI: honest-fix and injection VERIFIED with the same verdicts, silent-change and false-claim FLAGGED |
| G06 | 2026-10-16 | [#66](https://github.com/DuhItzAniket/Repro/pull/66) | After the review, the differences no verdict accounts for go to one gap reviewer (gap/1) with the supported claims that describe a change; its mapping is checked like a verdict and becomes supporting verdicts citing what they explain (ADR-0015); unexplained differences flag, no call when nothing could be explained; relevance through test selection; Groq's off-schema 400 asked again; record mode keeps valid cassettes; re-recorded: honest-fix and injection VERIFIED, silent-change and false-claim FLAGGED |
| G07 | 2026-10-16 | [#67](https://github.com/DuhItzAniket/Repro/pull/67) | With agents on, a claim-blind probe writer (probe/1) proposes inputs as data for the functions E06 can probe; proposals that do not fit the signature are dropped and counted, the rest run beside Hypothesis's on base, base again and head, and each probe_diff says which inputs found it (found_by); agent_probes switches it off; new fixture probe-semantic (VERIFIED without agents); live: 8 proposed, 0 dropped, 3 differ (+05:30, -04:45, +02:15); the Gate G runs replay with the probe writer off until re-recorded |
| G08 | 2026-10-17 | [#68](https://github.com/DuhItzAniket/Repro/pull/68) | Contradicted verdicts judged again with the evidence, other claims and ID enum reversed (agent_order_swap), degrading to unverifiable on disagreement; agreement on every verdict; optional 2-of-3 panel (agent_panel); judge_is_author on the card; QA agent (qa/1) withholds supported verdicts that cannot hold together; cassette keys now normalise IDs at line starts; recorded live: honest-fix and injection VERIFIED, silent-change, false-claim and probe-semantic FLAGGED; flip rate 0 of 13 |
| G09 | 2026-10-17 | [#69](https://github.com/DuhItzAniket/Repro/pull/69) | Verdict labels (13) in the claim set; repro_bench.runs runs the five reviewed PRs end to end (replay, record, live); repro_bench.verdicts measures verdict accuracy, unsafe verdicts, status accuracy, citation validity and rejection rate per answer, flip rate, cost; agent-evals.csv one row per metric; every refused answer a verdict.rejected; agent evals CI job against proposed [verdicts] thresholds; measured: 10/13 verdicts, 0 unsafe, 5/5 cards, 28/28 citations valid, 0/13 flips, $0.0089 per PR |
| Gate G | 2026-10-17 | [#70](https://github.com/DuhItzAniket/Repro/pull/70) | Stage G complete; release v0.7.0 ([gate record](phases/G-gate.md)); replayed offline with every agent on, the honest fix is VERIFIED and the unclaimed change and the false claim FLAGGED, each verdict citing engine evidence; the eval set meets the owner-approved thresholds (claims P 0.958 / R 1.000; verdicts 10/13, 0 unsafe, cards 5/5, citations 28/28, flips 0/13) |
| H01 | 2026-10-17 | [#71](https://github.com/DuhItzAniket/Repro/pull/71) | The failure catalogue (docs/FAILURES.md) with chaos tests through repro run on Ubuntu and Windows (a broken import, a hang, memory exhausted, a build that cannot work offline, an unreachable API, Docker or git missing); a selected test no run reported makes the change UNVERIFIABLE; memory_limit missing reason; test_timeout_s and memory_limit_mb settings; readable reasons (the error's first line, home written ~, resolutions on the Markdown card); retries for an unreachable API and unusable answers |
| H02 | 2026-10-17 | [#72](https://github.com/DuhItzAniket/Repro/pull/72) | repro_bench.scale on three pinned Pallets changes (itsdangerous, flask, click), cold and warm, with --check and a nightly job; cold 38 s -> 18 s, 402 s -> 110 s, 1,151 s -> 34 s: test-only dependency groups, the project's pytest selection kept, paired side runs, the timing pass pruned; cached-map runs report what cold runs do; a non-UTF-8 file name and pre-1980 tar times (flit_core in containers) fixed |
| H03 | 2026-10-17 | [#73](https://github.com/DuhItzAniket/Repro/pull/73) | THREAT_MODEL section 7 traces each of T1-T19 to its tests or residual (test_threat_model.py); the API's session token (URL fragment, sessionStorage, bearer header, no access log, browser opened on a private page); docker_runtime runsc = gVisor L4 with a CI job; seven more container escapes; the audit hook judges writes by resolved paths; home directory written ~ in events, logs scrubbed, an end-to-end leak audit; T6's first test; a dependency audit job (pip-audit, pnpm audit) |
| H04 | 2026-10-17 | [#74](https://github.com/DuhItzAniket/Repro/pull/74) | repro export --format in-toto|sarif|junit, each checked against its schema: an unsigned in-toto Statement v1 with Repro's predicate v0.1 (the card and its digest, cited evidence as digested resource descriptors, the event log's digest; card -> attestation -> card exact, tampering refused), predicate schema published with its description; SARIF 2.1.0 (a result per gap item, failed invariant, judged claim and missing item; kind/level as the spec pairs them); JUnit XML (a test case per claim, one for the status) |
| H05 | 2026-10-17 | [#75](https://github.com/DuhItzAniket/Repro/pull/75) | Release wheels (repro-engine with the cockpit inside, repro-events with the schemas; scripts/build_wheels.py); the README's install, one uv tool install from the release's wheels; scripts/install_check.py follows it into an isolated tool directory and makes a first run, inside ten minutes, in CI's install job on Ubuntu and Windows; release.yml attaches the wheels to a published release; repro doctor reports the cockpit; owner's decisions: repro-engine, GitHub releases now, PyPI later |
| H06 | 2026-10-17 | [#76](https://github.com/DuhItzAniket/Repro/pull/76) | The user guide (quickstart, reading a card, repro.yml, troubleshooting, for contributors) and a recorded demo (silent-change, stills from its replay); two reviewer dry runs from the docs alone, the second with no blocker; found and fixed: an installed Repro ran no test in Docker on Windows (hook now in a volume), a card VERIFIED with no test run (run_incomplete, UNVERIFIABLE), the session token in uvicorn's log, class-body changes selecting no test; install check requires a VERIFIED run |
| Gate H | 2026-10-17 | [#77](https://github.com/DuhItzAniket/Repro/pull/77) | Stage H complete; release v1.0.0 ([gate record](phases/H-gate.md)); the release's wheels install and make a first run, VERIFIED with a test run, on Ubuntu (L3, 27.2 s) and Windows (L2, 30.1 s) in CI; every threat traced to tests or a residual (H03); the user guide completed from the docs alone by a reviewer new to the project (H06) |
| I01 | 2026-10-17 | [#78](https://github.com/DuhItzAniket/Repro/pull/78) | repro run --pr <url>: the pull request read from GitHub's REST API (2026-03-10; token from GITHUB_TOKEN or GH_TOKEN, dropped on a cross-host redirect), its base branch and refs/pull/<n>/head (forks included) fetched into Repro's own bare mirror with the token in GIT_CONFIG_* only, head compared with the merge base; run.started records repo and PR; --repo uses a user's clone as is; failures exit 2 in words; checked on pallets/itsdangerous#378 |
| I02 | 2026-10-17 | [#79](https://github.com/DuhItzAniket/Repro/pull/79) | repro report <run>: the card on its GitHub pull request, checked against its log, no target code run; the check run Repro on the head commit (VERIFIED success, FLAGGED failure, UNVERIFIABLE neutral; annotations at the changed code, 50 a request), one comment found by a hidden marker and its author, updated in place, the job summary in Actions; a rerun updates; a target it cannot update is named and the others made |
| I03 | 2026-10-17 | [#80](https://github.com/DuhItzAniket/Repro/pull/80) | action.yml (composite): Repro from the Action's own checkout on the runner, containers only (REPRO_SANDBOX=docker), card kept as an artifact, reported, and on public repositories signed with actions/attest (card.json, the card's canonical bytes, as subject; Repro's predicate); actions pinned to commits; mirror and maps cached; forks not posted to unless opted in under pull_request_target (owner's decisions); plan/run/report in repro.integrations.github.action, tested; THREAT_MODEL T20 |
| Gate I | 2026-10-18 | [#81](https://github.com/DuhItzAniket/Repro/pull/81) | Stage I complete; release v1.1.0 ([gate record](phases/I-gate.md)); on the public test repository DuhItzAniket/repro-gate-i, opening a pull request made the check run (FLAGGED, one annotation), one comment and a signed attestation of the card (gh attestation verify passes), and re-running left one of each; I04 deferred |
| J01 | 2026-10-18 | [#82](https://github.com/DuhItzAniket/Repro/pull/82) | SWE-format adapter and sealed inputs, manifest by digest, canary input-contract test; first real instance found an interpreter constraint for J04 |
| J02 | 2026-10-18 | [#83](https://github.com/DuhItzAniket/Repro/pull/83) | Tier A (hidden tests and full suite in the task's image, SWE-bench grading) and tier B (UTBoost and written differential tests, 20 stable reruns); pipeline with 15% audits; blinded annotation page; kappa per source, adjudication, label freeze; guideline v1 |
| J03 | 2026-10-18 | [#84](https://github.com/DuhItzAniket/Repro/pull/84) | B0 CI-only in the task's image, B1 diff-only and B2 agentic static reviewers (Repro's model and budget), B3 PR-Agent v0.48.0 pinned; rules pre-registered in BASELINES.md; Testora not reproducible, reasons recorded |
| J04 | 2026-10-18 | [#85](https://github.com/DuhItzAniket/Repro/pull/85) | S0 shake-out on two fresh tasks in their own images (sandbox_image; three faults fixed); exact McNemar power (168 vs 155); memorisation probe and seeding tools; S2 inclusion decided (Python >= 3.12); S1 pilot not run within the free tier; budget open for the owner |
| J05 | 2026-10-18 | [#86](https://github.com/DuhItzAniket/Repro/pull/86) | S2's 351 candidates pinned; repair2 scaffold (two calls, fits the free tier) with gpt-oss-120b and qwen3.8-27b; pilot on two tasks graded by tier A; mini-SWE-agent specified for a paid plan |
| J06 | 2026-10-18 | [#87](https://github.com/DuhItzAniket/Repro/pull/87) | Prepared and paused: the registered analysis as code (exact McNemar with Holm, cluster bootstrap, coverage and bounding conversions) and PREREGISTRATION.md with the owner's freeze checklist; labels and budget wait for the owner |
| K01 | 2026-10-18 | [#88](https://github.com/DuhItzAniket/Repro/pull/88) | JavaScript/TypeScript outcomes (repro js-run: Vitest/Jest, lockfile installs with scripts off, process sandbox); ADR-0017 proposed for the V8 map, tracer and card |
| K02 | 2026-10-18 | [#89](https://github.com/DuhItzAniket/Repro/pull/89) | Fleet view (/fleet.html, GET /fleet): live tiles per run, motion only for events, opens the cockpit; checked in a browser |
| K03 | 2026-10-18 | [#90](https://github.com/DuhItzAniket/Repro/pull/90) | repro mcp: MCP server on stdio (official SDK); start a verification by commits, read status, card and evidence; run --no-serve prints its run ID at start |
| K04 | 2026-10-18 | [#91](https://github.com/DuhItzAniket/Repro/pull/91) | repro symbolic: CrossHair diffbehavior on small pure annotated functions a change edits, base body vs head's in head's environment; card evidence needs a schema change |
| K05 | 2026-10-18 | [#92](https://github.com/DuhItzAniket/Repro/pull/92) | docker_context setting (a team's engine elsewhere); ADR-0018 proposed for E2B self-hosted and Kata with Firecracker |
| J09 | 2026-10-18 | [#93](https://github.com/DuhItzAniket/Repro/pull/93) | Prepared and paused: kick-the-tires script (2 min 10 s, four checks), AE guide, .zenodo.json and CITATION.cff; study data and DOI wait |
| J10 | 2026-10-18 | [#94](https://github.com/DuhItzAniket/Repro/pull/94) | Prepared and paused: paper draft 0 (results pending) and the submission checklist; six verified references added |
| Release 1.2.0 | 2026-10-20 | [#98](https://github.com/DuhItzAniket/Repro/pull/98) | Stage K's first items (K01–K05), the study's harness (J01–J05) and the demo's fixes (#95, #96, #97); first PyPI release; the study (J06–J10) deferred; ADR-0017 and ADR-0018 accepted (K06–K10) |
| K06 | 2026-10-20 | [#100](https://github.com/DuhItzAniket/Repro/pull/100) | The test → function map for Vitest and Jest (V8 precise coverage per test from a setup file added to the project's own); changed functions by a conservative scanner; D06's rule; js-run reports the selected tests and why |
| K07 | 2026-10-20 | [#105](https://github.com/DuhItzAniket/Repro/pull/105) | JavaScript function-level traces: repro_encode's rules ported (no user code runs), the changed functions wrapped (a Vite plugin in place; Jest through its modules' exports), base twice (shuffled) and head compared by E03's comparison; js-run reports behaviour differences with values |
| K08 | 2026-10-20 | [#107](https://github.com/DuhItzAniket/Repro/pull/107) | `repro run` on JavaScript and TypeScript (Vitest, Jest): the language in `run.started`, a scanner analysis, A, H, A2 shuffled and reruns, outcomes, edited tests, behaviour, dependencies and invariants as Python's events; L2, the rest missing items (K11); unjs/defu's silent guard change FLAGGED |
| K11 | 2026-10-20 | [#110](https://github.com/DuhItzAniket/Repro/pull/110) | JavaScript at L3 in Repro's Node image (own install container; `.pnpmfile.cjs` and `yarn-path` found running under `--ignore-scripts`, turned off); the public API and edited tests' assertions from a pinned TypeScript 6.0.3; base's version of an edited test on head |
| Release 1.3.0 | 2026-10-20 | [#111](https://github.com/DuhItzAniket/Repro/pull/111) | JavaScript and TypeScript end to end (K06–K08, K11), the fixes #106, #108 and #109, Dependabot's updates; published to PyPI |
| Release 1.3.1 | 2026-10-21 | [#114](https://github.com/DuhItzAniket/Repro/pull/114) | A patch release with no code change: CI's timeout (#112), the Marketplace docs (#113); the first release archived on Zenodo, for a DOI |
| K10 | 2026-10-21 | [#116](https://github.com/DuhItzAniket/Repro/pull/116) | Isolation level L5: each container in a Kata microVM with Cloud Hypervisor, under Docker (`docker_runtime = "kata"`), the level probed from the container's kernel; uid 1000's `nproc` as the process limit there (Kata's VM applies no pids limit, measured); `scripts/kata/setup.sh`, tested in WSL2 and in CI's `kata` job on GitHub's nested virtualization |
| I04 | 2026-10-21 | [#117](https://github.com/DuhItzAniket/Repro/pull/117) | The GitHub App (ADR-0019): `repro app register` from a manifest (the key and secret straight into the data directory), `repro app serve` from a smee.io relay or a port; deliveries checked by signature and ID; a repository's pull requests run, a fork's on a write user's click; a one-repository token per run, in Docker; the card with the App's ID, its bot's comment and a Re-run button. Live trial in WSL2 at L5: 11 relayed deliveries all checked, FLAGGED in 72 s; a re-run fault found and fixed (a new check run each run) |
| Release 1.4.0 | 2026-10-22 | [#118](https://github.com/DuhItzAniket/Repro/pull/118) | Isolation level L5 in Kata microVMs (K10) and the GitHub App (I04); published to PyPI and archived on Zenodo |
| K09 | 2026-10-23 | [#119](https://github.com/DuhItzAniket/Repro/pull/119) | The E2B sandbox (`sandbox = "e2b"`, ADR-0018 B): a Firecracker microVM per side from a template of Repro's image recipe; sealed by the VM's own nftables before the target runs, as a user without sudo, and checked from inside (E2B's own firewall let TCP to numbered addresses through, measured on WSL2); RemoteSandbox shared with Docker's; the e2b extra; live in WSL2 (19 tests) and CI's `e2b` job |
| Release 1.5.0 | 2026-10-25 | [#123](https://github.com/DuhItzAniket/Repro/pull/123) | The E2B sandbox (K09), runs a killed process left "running" ended as failed (#120), fast-uri 3.1.9 (#121), the README and PyPI pages (#122); published to PyPI and archived on Zenodo |
| Release 1.6.0 | 2026-10-31 | [#134](https://github.com/DuhItzAniket/Repro/pull/134) | The selected tests as graph nodes on the stage (#131, B14), Claude reviewers asked for their answer when out of turns (#132, G01), image build errors that name their cause (#126, D03), the documentation fast path in CI (#124) |
