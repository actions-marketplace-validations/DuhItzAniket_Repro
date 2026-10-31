# Repro — Architecture

> Status: **baseline v1** (phase A01, 2026-10-13). This document is the target architecture.
> Decisions that are expensive to reverse are recorded as ADRs in [`adr/`](adr/); this document
> links them. Facts about third-party tools were verified on 2026-10-13, and the sources are in
> [`research/`](research/).

## 1. Purpose and quality attributes

Repro takes a code change (base ref, head ref, and the change's natural-language description).
It executes both sides in isolation and records what each side *does*. From those
observations it produces evidence, judges the change's claims against that evidence, and
publishes a **Reproduction Card**.

Quality attributes, in priority order. When two conflict, the higher one wins.

| # | Attribute | What it means here |
|---|---|---|
| Q1 | **Honesty** | Nothing is reported as verified unless evidence supports it. Anything that could not be checked appears as an explicit *missing* item. The isolation level actually achieved is stated on the card. |
| Q2 | **Correctness of evidence** | Behaviour differences are real, not noise. Non-determinism is measured (A/A′ runs), not guessed. |
| Q3 | **Isolation** | Target code is untrusted. It never runs in the host's Python, never touches the host repo's `.git`, and runs without network wherever the platform can enforce that. |
| Q4 | **Replayability** | Every run can be reconstructed from its stored event log. The UI and the card are pure projections of that log. |
| Q5 | **Explainability** | Every verdict cites evidence IDs. Every evidence item links to raw artifacts with content digests. |
| Q6 | **Portability** | The engine runs on Windows x64 and Linux, and CI covers both. Target execution happens inside a Linux container when one is available. |
| Q7 | **Performance** | A typical small-to-medium Python PR is verified in minutes, and the cockpit renders at 60 fps (see DESIGN.md budget). |

## 2. System context

```
                    ┌──────────────────────────────────────────────────────────────┐
  developer ──CLI──►│                          Repro                               │
  (or CI)           │                                                              │
                    │  engine ──► sandbox (Docker / process) ──► target repo code  │
  web browser ◄─WS──│     │                                                        │
                    │     ├──► Anthropic API (reviewer agents; optional)           │
                    │     └──► GitHub API (PR ingestion + reporting; Stage I)      │
                    └──────────────────────────────────────────────────────────────┘
  inputs:  local git repo + base/head refs + PR description   (or a PR URL, Stage I)
  outputs: event log, evidence store, Reproduction Card (MD / JSON / in-toto / SARIF / JUnit)
```

External dependencies and how much Repro depends on each:

| Dependency | Required? | Without it |
|---|---|---|
| git ≥ 2.40 | yes | — |
| uv (pinned version) | yes | — |
| Python ≥ 3.12 for the engine | yes | — |
| Python ≥ 3.12 **in the target environment** | for behaviour traces | Tests still run. Traces become a `missing` item (`trace_unavailable`). |
| Docker (Desktop on Windows/macOS, Engine on Linux) | strongly recommended | Falls back to process isolation. Card states `network: detected-only`, `isolation: process`. |
| Anthropic API key | optional | Deterministic-only mode: no claim extraction or judging. The card shows deterministic evidence only, and claims are `unverifiable`. |

## 3. Component view

```
repro/
├─ packages/events/            THE contract: JSON Schema (2020-12) + generated TS & Pydantic types
├─ engine/src/repro/            (import package `repro`; distribution `repro-engine`)
│   ├─ cli.py                  Typer: run · replay · serve · doctor · export
│   ├─ config/                 engine settings (env/file), repro.yml loader + schema
│   ├─ orchestrator/           run lifecycle, stage DAG, job runner, cancellation, timeouts
│   ├─ bus/                    event construction, seq assignment, single-writer persistence, fan-out
│   ├─ store/                  SQLite (runs/events/evidence/cards), artifact store, migrations
│   ├─ api/                    FastAPI REST + WebSocket (replay-then-live)
│   ├─ gitops/                 ref resolution, diff parsing, worktrees, .git-less snapshots
│   ├─ analysis/               symbol table (ast), changed-lines→symbols, import graph (grimp),
│   │                          blast radius, API extraction (griffe)
│   ├─ sandbox/                isolation backends: process (L0–L2), docker (L3; L4 with gVisor, L5 with Kata)
│   ├─ envs/                   project-type detection, interpreter choice, uv provisioning, env cache
│   ├─ runner/                 test selection, two-sided execution, reruns, perf passes
│   ├─ plugin/                 the Repro pytest plugin (shipped INTO the sandbox):
│   │                          selection collector, tracer, canonical encoder, net guard, audit hook
│   ├─ diff/                   outcome, behaviour, API, dependency, perf differs
│   ├─ policy/                 invariants, status rules, missing taxonomy
│   ├─ agents/                 LLM backend, locked-down runtime, tools, prompts, validators, cassettes
│   ├─ card/                   card assembly + exporters (md, json, in-toto, sarif, junit)
│   └─ integrations/github/    PR ingestion, check runs, sticky comments (Stage I)
├─ engine/tests/               unit, property, integration (scripted fixture repos), sandbox security
├─ apps/web/                   Vite + TS cockpit (see §13)
├─ bench/                      evaluation harness: dataset adapters, baselines, analysis (Stage J)
└─ docs/                       this documentation set
```

Dependency rule: `plugin/` is **stdlib-only**. It runs inside arbitrary target environments
with Python 3.12–3.15, so it cannot import engine code or third-party packages. It talks to the
engine through files written to an output directory that the engine mounts or collects.
As built (D04): the plugin is `sandbox/_hook/repro_pytest.py`, beside the audit hook, in the
directory both sandboxes put first on `PYTHONPATH`; besides the standard library it imports only
pytest, which is always there.

## 4. Run pipeline

The seven **stages** drive the UI progress bar and the `stage` field of every event. Inside
them, the engine runs several **passes**, each with an explicit trust level.

| Stage | Pass | Where | Executes target code? | Output |
|---|---|---|---|---|
| **read** | Ingest | host | no | PR metadata, diff, description |
| | Claim extraction | host → LLM | no | `claim.extracted` (atomic claims with targets and kind) |
| **build** | Prepare | host | no | two worktrees (`git -c core.longpaths=true worktree add --detach`) and two `.git`-less snapshots (`git archive`) |
| | Static analysis | host | **no** (griffe `allow_inspection=False`) | symbols, changed symbols, import graph, blast radius, API surface |
| | Provision | sandbox (egress limited to the package index, or offline from prefetched wheels) | **yes** (build backends, `setup.py`) | one environment per side, same interpreter version |
| **run** | Selection map | sandbox, network off | yes | test → functions-touched map (base; head for new/changed tests) |
| | Trace runs A, A′, H | sandbox, network off | yes | outcomes + behaviour observations: base, base again (different seed and order), head |
| | Probe pass | host → LLM (writing), then sandbox, network off (running) | yes | generated differential tests aimed at changed functions, run on both sides (§4.4) |
| | Perf pass | sandbox, network off, untraced | yes | interleaved timings (ABBA) |
| **diff** | Differ | host | no | outcome / behaviour / API / dependency / perf diffs → evidence |
| **check** | Policy | host | no | invariant results → evidence |
| **review** | Judgement | host → LLM | no | per-claim verdicts and an explained-by map, all citing evidence |
| **card** | Status | host | no | deterministic status computation → card → exports |

Determinism controls applied to every sandboxed run: fixed `PYTHONHASHSEED` (A and H use
the same seed; A′ uses a different one), `-p no:randomly` (or a fixed `--randomly-seed`),
no xdist (Repro shards deterministically itself if needed), `-o addopts=` override with
Repro's own flags, `PYTHONUTF8=1`, pinned `TZ`/locale, and a minimal environment with no host
secrets. As built (D04, `repro.runner`): `-p no:randomly -p no:xdist -p no:cacheprovider
--continue-on-collection-errors`, `TZ=UTC`, `LC_ALL=C.UTF-8`, `random` seeded per test from the
run's seed and the node ID, and the plugin's net guard (loopback only) on every level.
Overriding `addopts` also dropped the project's own choice of tests (click's `-m 'not stress'`
leaves out about 31,000 cases). Since H02 (`runner/pytest_config`), the selection options of the
project's `addopts` (`-m`, `-k`, `--ignore`, `--ignore-glob`, `--deselect`, `--import-mode`,
`-W`, `--doctest-modules`, `--doctest-glob`, `--strict-markers`, `--strict-config`, `-p no:…`)
are read from the file pytest itself would use and passed again; output, plugin and reporting
options are not.

**Ingest from a pull request** (I01, `integrations/github`). `repro run --pr <url>` reads the
pull request from GitHub's REST API (version `2026-03-10`; a token from `GITHUB_TOKEN`, else
`GH_TOKEN`, when set) before the run starts. Its commits are fetched into Repro's own bare
mirror under the data directory, never the user's clone: the base branch and
`refs/pull/<n>/head`, which GitHub keeps on the base repository for a fork's pull request too.
Head is the pull request's head commit; base is the merge base of its base and head, the
change the pull request itself makes. `run.started` carries the repository and the pull
request (title, number, URL), and the title and description are the reader's PR text. With
`--repo`, a local clone is used as it is, and nothing is fetched into it.

**Report to a pull request** (I02, `integrations/github/report`). `repro report <run>` takes
the card as `repro export` does, checked against its log, and makes or updates three targets:
the check run `Repro` on the head commit (the status as its conclusion, the card as its
text, annotations at the changed code a flagged difference or an unreached function
concerns, 50 a request); one comment, found by a hidden marker and written by the same
identity; and, in GitHub Actions, the job's summary. It runs no target code, so it can report
a run made elsewhere: a fork's pull request gets a read-only token where its code runs.

**The Action** (I03, `action.yml`, `integrations/github/action`). A composite action: Repro
runs on the runner from the Action's own checkout, and its sandbox uses the runner's Docker
(L3; L4 under gVisor). It plans (a fork's pull request is not posted to unless the workflow
opts in under `pull_request_target`; only a public repository's posted run is signed), runs
Repro on the pull request, keeps the card as an artifact, reports it, and signs `card.json`
with Repro's predicate through `actions/attest`. Repro's mirror and test maps are cached
between runs.

**The GitHub App** (I04, ADR-0019, `integrations/github/app*`, `webhook`, `relay`). `repro app
register` registers it from a manifest: GitHub hands its private key and webhook secret
straight to Repro's data directory (`github-app/`). `repro app serve` takes deliveries from a
smee.io relay or a port of its own:
- each is checked by its signature, then by its delivery ID against those seen before;
- a pull request from the repository is queued, and a fork's gets a check run asking for a
  click. A click or a re-run is run only for someone with `admin` or `write`;
- a worker runs `repro run --pr` in a process of its own, with a token for that one
  repository, in Docker, and reports the card as I02 does, with the App's ID, its bot's
  comment and a **Re-run Repro** button.

The App runs on the operator's machine, so its runs can be L5 (K10). Its cards are not
signed: signing rests on GitHub Actions' identity.

**JavaScript and TypeScript** (K08, ADR-0017). `run.started` records the target's language,
read from the repository's root (`language.detect`) or given with `--language`. A JavaScript
run has its own analysis (`analysis/javascript.py`, §9) and its own run and diff stages
(`js_stage.py`, with `jsts`, `jsmap` and `jstrace`). They emit the same events, so the status
rules, the card, the exports and the cockpit are shared. The runs:
- A and H run the whole suite, with the test → function map (V8 precise coverage per test) and
  the traces (the changed functions wrapped at the runner's own transform point);
- the selection follows from both maps (§4.1's rule);
- A2 runs shuffled with a fixed seed, and a flip is rerun three times on each side.

Since K11 it runs as Python's does: in a container with Node and no network (L3, L4 under
gVisor) when Docker answers, else in the process sandbox (L2), where the network is a missing
item. The install runs in a container of its own, the only one with the network, with lifecycle
scripts, pnpm's `.pnpmfile.cjs` and Yarn's `yarn-path` all off. The public API and edited
tests' assertions come from a pinned TypeScript compiler (§9), and base's version of an edited
test runs against head. Timing is still Python's alone, and a missing item.

### 4.1 Test selection (`runner/selection`)
Selection uses a **function-level per-test map** built by Repro's own `sys.monitoring` collector
in "collect-map" mode. It does not use coverage.py contexts, because those force coverage's
slower settrace core and cannot use the `sysmon` core (see research/03 §2).

```
selected = tests touching any blast-radius function            (dynamic map, base)
         ∪ tests importing a module with module-level changes  (grimp downstream)
         ∪ tests added or modified by the change               (always run on both sides)
```

The map is cached keyed by `(base commit, lockfile digest, interpreter)`. If a change has no
selected tests, the card gets a `no_covering_test` missing item for each uncovered changed symbol.

As built (D05, `runner/selection.py`): the map records, per test, each function's *place* (file
relative to the snapshot, qualified name, first line), which the engine resolves to symbols; the
cache key adds the platform and the map's format. Module-level code runs at import, so its
changes select by imports (and a changed `conftest.py` selects the tests below it). A changed
symbol is covered when a selected test ran it on base or, mapped again, on head; every other
changed symbol outside test code becomes a `no_covering_test` item.

### 4.2 Outcome classification (`diff/outcome`)
Every selected test is classified on base vs head: **F2P** (fail→pass), **P2P**, **P2F**,
**F2F**, **new**, **removed**, **modified** (test code differs between sides, so outcomes
are not directly comparable), or **flaky** (A and A′ disagree, or reruns don't reach quorum).
P2F and F2P flips are re-run *k* times on both sides before they become evidence. An F2P test
whose target matches a "fixes …" claim is the strongest supporting evidence Repro produces.

Two further checks come from the literature (research/02 §1.9, §5.5):
- **Test tampering.** Agents are known to edit or delete tests to make them pass
  (ImpossibleBench). Repro therefore diffs the test files themselves. Removed tests, newly added
  `skip`/`xfail` markers, deleted or loosened assertions, and widened `pytest.raises` become
  `test_change` evidence. Repro also runs the **base version of each modified test against
  head**. If the old test fails on head, that is a P2F on the unmodified test.
- **DeFlaker check.** A test that newly fails without executing any changed code (per the
  selection map) is marked *likely flaky* before any rerun is spent on it.

As built (D06, `runner/outcomes.py`, `runner/testdiff.py`, `runner/twosided.py`, `run_stage.py`):
A, A2 (A′: reverse order, `PYTHONHASHSEED=1`) and H; a test passes when `passed`, `xfailed` or
`xpassed` (a skip does not pass); flips are rerun k = 3 times on both sides and DeFlaker's label
stays on the evidence rather than replacing the reruns; the test diff adds `assertion_weakened`
(an exact check made looser) and `raises_widened`. The pipeline's run stage provisions both
sides, runs them and emits `test.selected`, `test.started` / `test.finished`, `test_outcome` /
`test_change` evidence with `diff.outcome` / `diff.tests`, and the missing items.

### 4.3 Behaviour tracing (`plugin/tracer`, `plugin/encoder`) — see ADR-0005
- `sys.monitoring` tool, claimed in order **4, 3, 2, 5**. IDs 0 (debugger) and 1 (coverage) are
  avoided. If no ID is free, the result is an explicit `trace_unavailable` missing item.
- Global `PY_START`. Non-target code objects return `DISABLE` once. Target code objects get local
  `PY_RETURN | PY_YIELD | PY_RESUME`. `PY_UNWIND` is filtered by code object (it can only be
  global before 3.15).
- Arguments are read at `PY_START` from the callee frame (`sys._getframe(1)`), and results come
  from the `PY_RETURN` callback. In-flight calls are keyed by `id(frame)` with per-thread stacks.
  Code objects are kept alive to prevent `id` reuse.
- Records are attributed to the current test and phase through pytest hooks and flushed per test
  to the output directory.
- **Budgets.** Each (test, function) pair gets *N* fully encoded calls (default 200, measured in A08). After that only counts and
  rolling digests are kept, then the location is disabled and marked `truncated`. The per-call
  overhead of `sys.monitoring` with Python callbacks is **not published** (research/03 §1.3),
  so Repro measured it in A08 (`spikes/A08/RESULTS.md`). Dispatch costs 114–197 ns per call,
  capture 2.3–11.4 µs per call (dominated by encoding), and budgeted capture runs at about
  baseline speed.

As built (E01, `sandbox/_hook/repro_trace.py`): the plugin's one tool serves the map and the
tracer; targets are named by file, qualified name and line span; outcomes are `return`,
`raise`, `suspended` (a generator that did not run to its end) and `unfinished`. When a target's
budget runs out, its recorded calls still in flight end as `unfinished` and its return and yield
events are disabled too: keeping them for a recursion's outer frames cost 27x in the E01
benchmark.

**Canonical encoder.** A structural, CBOR-like tagged encoding is hashed with BLAKE2b-128.
It **never calls user-defined dunders** (`__repr__`, `__eq__`, properties, iterators). It
normalises paths (`<ROOT>`, `<TMP>`, `<HOME>`, `/`) and memory addresses. Floats use
`float.hex()` with explicit NaN and −0.0 tokens. Sets are sorted by encoded bytes. Dicts get
ordered and order-insensitive digests. Generators, files, sockets and locks are opaque, and
exceptions are recorded as type plus args plus cause chain. Cycle, depth, node and time guards
apply. A truncated or failed encoding is never treated as equal to anything.

As built (E02, `sandbox/_hook/repro_encode.py`): an object's state is read through descriptors
taken from `type`, `object` and `BaseException` themselves and applied to `type(value)`, so no
metaclass, `__getattribute__`, `__class__` property or other dunder of the target runs; a class
that puts its own descriptor in place of `__dict__` or a slot is encoded opaque instead. Builtin
subclasses keep their builtin value (read through the base type's own methods and iterators,
never rebuilt). Adapters cover only modules the target already imported. Dictionaries keep
insertion order (the same in every process). For results, the same walk also gives a digest
with every dictionary sorted, recorded only where it differs; arguments, the join keys, built
the same way by the same test on both sides, skip that sorting for speed.

**Stability.** Fields that differ between A and A′ (same code, different seed and order) are
*unstable*. They are masked from the A-vs-H comparison and listed on the card. This is the
Diffy noise-floor idea (research/01 §2.2) applied at the function level.

As built (E03, `runner/observations.py`): A, A′ and H trace the changed functions that exist
on both sides, filed under the change's symbol ID; A′ differs from A in test order,
`PYTHONHASHSEED` and the `random` seed. A call is keyed by `(test, phase, symbol, arguments'
digest, occurrence)`. The fields compared are `outcome`, `type`, `value` (the result with
dictionaries sorted), `order` (the result as it was) and `yields`; a field that differs between
A and A′ is masked for that call, and a call set that differs (arguments from the clock or a
random source) masks the calls on one side only and every field of the calls A′ did not make.
Incomplete encodings, `unfinished` calls and spent budgets are never compared. A call whose
*arguments* could not be encoded (a framework's context object: too deep, too large or cyclic)
has no such key; it is keyed by its position instead, the n-th such call of the function in
the test's phase, when A, A′ and H made as many of them (`align`). Its result is compared like
any other, A and A′ telling whether that order is noise, and the tracer previews it on request
as `#n`. Otherwise it stays unkeyed (`encoding_failed`). Test code is traced but left out. The trace has its own file in the sandbox (`calls.jsonl`, 64 MiB at most),
so it never crowds out the test results.

As built (E07): A, A′ and H also preview each test's first four calls of each traced function
(`REPRO_TRACE_SAMPLES`), and the engine emits them as `trace.sample` events (`base`,
`base_repeat`, `head`) as each run ends, test code left out, at most 2,000 a run: the samples
the cockpit's behaviour field and Signals draw before the diff stage's verdicts arrive.

### 4.4 Differential probes (`runner/probes`, Stage G)
Re-running existing tests misses most divergences. Shadow symbolic execution observed that
patch tests "often exercise the same behaviour in the old and the new version". PatchDiff,
Testora and BERT all needed *generated* tests to expose differences (research/02 §3.3, §3.7, gap
analysis). Repro adds a probe pass:
1. A **probe-writer agent** gets the changed functions' signatures and bodies on both sides,
   plus nearby existing tests. It writes pytest probes that call those functions with concrete
   inputs. It sees no claims, so it cannot write probes that merely confirm them.
2. Probes are filtered with the TestGen-LLM pattern (research/02 §9.1). A probe must collect,
   run on base, and be deterministic across A and A′. Probes that fail these filters are
   discarded and counted.
3. Surviving probes run on head under the tracer. A differing result is `probe_diff`
   evidence, tagged `provenance: generated`. Its reproducer is the probe source itself.

For annotated pure functions, a deterministic alternative is input replay. Hypothesis-generated
JSON-able inputs are serialised on the host and replayed in both sandboxes. CrossHair
`diffbehavior` is optional and kept for later (K04). Probe evidence is reported separately from
evidence derived from existing tests. The card always shows which kind supports a finding.

As built (E06, `runner/probes.py`, `sandbox/_hook/repro_probe.py`): module-level functions the
change modified with the same JSON-typed parameters on both sides (read with `ast`), 40 inputs
each from Hypothesis (`derandomize=True`, no database), run in each side's sandbox by its
interpreter: base, base again with other hash and `random` seeds, head. Inputs on which the
base runs disagree are left out; differences are classed as E04's and grouped per function:
`probe_diff` with `provenance: generated`, `diff.probe` `warn`, the probes in
`obs/probes.cbor`. Timing (D07) runs after the probes and E04's reproducers.

As built (G07, `agents/prober.py`, prompt `probe/1`): the probe writer proposes **inputs, not
code**, for the functions E06 can probe. It is shown each one's definition on head, its
parameters' types, the shape its arguments must take and the change to its file, never the PR's
text or claims, and answers with up to 8 inputs per function (6 functions per call) as JSON
text. The filters, counted and logged: a proposal must name a probed function and exactly its
parameters, each value of its annotated JSON type within E06's bounds (`probes.fits`), or it is
dropped on the host; the rest, without repeats of Hypothesis's inputs, run after them in the
same three sandboxed runs, where an input the base runs disagree on is left out. Each
`probe_diff` says which generator's inputs differ (`found_by`: `hypothesis`, `agent` or both)
and how many inputs were proposed; its reproducer is a call, as for E06. Proposing data rather
than writing pytest probes keeps every agent-written byte out of the interpreter (T15) and
reuses E06's determinism and reproducers; functions E06 cannot probe (methods, objects, fixtures)
get no probes yet. A probe writer that fails is an `agent_failed` item; Hypothesis's inputs run
regardless.

### 4.5 Diffs (`diff/`)
| Differ | Input | Evidence produced |
|---|---|---|
| Outcome | per-test outcomes A, A′, H, reruns | `test_outcome` (F2P / P2F / flaky …) |
| Behaviour | observations joined on `(test, function, args_hash)` | `behaviour_diff`: different result, new or changed exception, input mutation, order-only. Each item has a minimal reproducer (test node ID plus encoded args preview) |
| API | griffe `find_breaking_changes(base, head)` plus Repro's annotation-string comparison of return and attribute types (griffe does not check these yet) | `api_diff` |
| Dependencies | lockfiles (uv.lock, poetry.lock, pdm.lock, requirements) **and** installed sets (`uv pip list --format json`) on both sides | `dep_diff`, `lock_drift`. As built (E05, `provision/deps.py`): the requirements D03 read (a lock's pins: `lockfile`; otherwise `declared`) and the installed distributions, listed in each side's sandbox by its own interpreter with `-I -S` (metadata only; the project's editable install left out); PEP 440 order for `upgraded` / `downgraded`; lock drift for installed packages off their pin. `diff.deps` is `warn`. F02: when the requirements come from a lock or requirement files, what the metadata (`[project]`, `setup.py`, `setup.cfg`) declares is compared too (`declared`); metadata that cannot be read statically, with no lock, is an `unsupported_project` item; the requirements are compared even for a side that could not be installed |
| Behaviour (as built, E04) | E03's comparison of A and H, noise masked | `behaviour_diff` per (test, function, class): `result_changed`, `exception_added` / `_removed` / `_changed`, `order_only`, `input_mutated` (the arguments, kept from PY_START, encoded again when the call ends), `calls_changed` (other arguments on one side). The tests with differences run again on both sides with previews of each group's first call: the reproducer is that run's call and results, and a difference that does not come back is `unstable`. `diff.behaviour` is `warn` (ADR-0015) |
| Perf | untraced interleaved runs with an A/A noise floor | `perf_diff` with ratio of medians, BCa bootstrap 95 % CI and noise floor. Tests below the timing floor are `perf_not_assessable`. As built (D07, `runner/perf.py`): the `P2P` tests, ABBA until 10 runs a side, call-phase times; a regression needs the CI above 1, the ratio past max(10 %, the A/A spread of base's own ABBA samples), and Mann-Whitney significant after Benjamini-Hochberg; 5 ms timing floor |

### 4.6 Card status rules (`policy/status`) — deterministic, no LLM
```
FLAGGED       if any: claim contradicted · unexplained stable behaviour or probe diff · P2F on an
              unmodified test (incl. base version of a modified test run on head) · unexplained
              test weakening/removal · invariant failed · API break not covered by a supported claim
UNVERIFIABLE  else if any: no extractable claim (and claims.require_explicit) · environment
              failed · missing coverage above threshold (config) · all reviewer verdicts rejected
VERIFIED      otherwise (all claims supported, no unexplained gap, invariants pass)
```
`FLAGGED` takes precedence over `UNVERIFIABLE`, because concrete evidence of a problem
outweighs incompleteness. Every gap item also carries an **origin**: *introduced* (absent on
base) or *pre-existing* (present on base, e.g. F2F). Repro can determine origin rigorously
because it actually ran base.

As built (F04, `repro/policy/status.py`, `repro/policy/gap.py`): the inputs are read from the
event log alone, so a replay decides the same status. Gap items are the `diff.*` checks other
than plain passes, plus head's `policy_change`; each resolves to pre-existing, unassessed
(unstable, flaky, modified, removed), passed (`ok` on its own), explained (a supported verdict
cites it) or flagged. A P2F, a new test failing on head, a base test failing on head and head's
policy edit flag whatever the claims say; any other difference flags unless a supported claim
explains it, so without a review stage every difference flags (ADR-0015 §3). VERIFIED needs
everything checked: an invariant not evaluated, a claim not supported, a job that failed or was
skipped, a side without an environment, an unanalysed change, tests that did not run, or more
changed symbols unverified than `max_uncovered_changed_symbols` allows each give UNVERIFIABLE.
A changed symbol is unverified when a `no_covering_test`, `unstable_observation`,
`encoding_failed` or `trace_unavailable` item names it (or names no symbol at all). The policy
in force is `policy` evidence on every run.

## 5. Event model — see ADR-0002 and ADR-0003

### 5.1 Envelope
```jsonc
{
  "id": "evt_01J…",            // ULID
  "run_id": "run_01J…",
  "seq": 1042,                 // monotonic per run, assigned by the single writer; replay order
  "ts": "2026-10-13T13:40:01.123Z",
  "stage": "read|build|run|diff|check|review|card",
  "agent": "a07",              // null for engine-internal events
  "kind": "test.finished",
  "target": { "file": "pkg/dates.py", "symbol": "parse_date", "test": "tests/test_dates.py::test_tz" },
  "status": "start|progress|ok|warn|fail",
  "payload": { },              // schema'd per kind
  // additive fields (ADR-0003):
  "v": 1,                      // envelope schema version
  "pv": 1,                     // payload schema version for this kind
  "parent": "evt_…",           // causation (optional)
  "producer": "repro/0.3.0+abc1234"
}
```
The core fields are unchanged from the original brief. The four additive fields were accepted
in ADR-0003. In schema v1, `v`, `pv` and `producer` are required (no older logs exist), and
`parent` is optional. The authoritative definition is `packages/events/schema/` (A04): the
envelope, one payload schema per kind, shared definitions, and the models (claim, evidence,
verdict, missing item, artifact, card). Execution sides are named `base`, `base_repeat` (A′) and
`head`.

### 5.2 Kinds (v1)
| Group | Kinds |
|---|---|
| Run | `run.started`, `run.finished`, `stage.started`, `stage.finished`, `log.line` |
| Agents | `agent.spawned`, `agent.state`, `cost.estimate` |
| Read | `claim.extracted` |
| Build | `snapshot.ready`, `graph.node`, `graph.edge`, `symbol.changed`, `blast.computed`, `sandbox.ready`, `env.provisioned` |
| Run | `test.selected`, `test.started`, `test.finished`, `trace.sample`, `perf.sample` |
| Diff | `diff.outcome`, `diff.tests`, `diff.behaviour`, `diff.probe`, `diff.api`, `diff.deps`, `diff.perf` |
| Check | `invariant.result` |
| Evidence | `evidence.created`, `missing.recorded` |
| Review | `verdict.issued`, `verdict.rejected` |
| Card | `card.published` |

Compared with the original brief, the new kinds are `stage.*`, `symbol.changed`,
`sandbox.ready`, `env.provisioned`, `perf.sample`, `diff.outcome`, `diff.tests`, `diff.probe`,
`missing.recorded`, `verdict.rejected` and `cost.estimate`. `worktree.ready` is renamed to `snapshot.ready`, because
target code runs from snapshots, not worktrees. Adding kinds does not change core fields.

The graph has two parts. At build: every module, every import and a node per changed function,
method or class. In the run stage, once the tests are selected and before their
`test.selected` events: a node per selected test (`type: test`, its ID the test ID that events
name in `target.test`) and a `covers` edge from it to each changed symbol's node that selected
it. So `graph.node` and `graph.edge` may come in `build` or `run`. These test nodes are what the
stage's bunches of strands fire from (B14). A run adds at most 600 of them, the stress fixture's
count that the cockpit's budget is measured with; past that, the tests selected for the most
changed symbols are drawn, and every selected test is still run. No check reads the graph: the
card, the gap rules and the agents' evidence are the same without it.

### 5.3 Rules
- **Append-only.** Corrections are new events (e.g. `verdict.rejected` follows a
  `verdict.issued` that failed validation). Stored events are never edited.
- **Schema evolution.** Consumers ignore unknown fields. Upcasters keyed by `(kind, pv)` live in
  `packages/events` so old JSONL logs keep replaying.
- **Result-bearing events (ADR-0015).** `diff.*` and `invariant.result` events are the run's
  *checks*. Their envelope `status` is `ok` for a passed check, `fail` for a problem on its own
  terms (P2F, test tampering, failed invariant), and `warn` for a difference that needs a claim
  to explain it or could not be compared. The cockpit's CHECKS and FLAGGED counters and the
  change×check index are computed from these rules alone.
- **Secrets.** Payloads never contain environment variables, API keys or tokens. The bus scrubs
  known secret patterns before persistence, as defence in depth, and writes the home directory
  as `~` in every string (H03): a card may be shared, and an absolute path names its user. Logs
  pass through the same scrubbing.
- **Interop (export only).** Lossless mapping to CloudEvents 1.0: `type = dev.repro.<kind>`,
  `source = /repro/runs/<run_id>`, `sequence` as a zero-padded string, and `target` mapped to
  `subject` (canonical string) with the full object kept in `data`. OpenTelemetry export is
  deferred until the GenAI semantic conventions leave "Development" status (research/04 §5.2).

## 6. Evidence model

```jsonc
{
  "id": "ev_01J…",
  "run_id": "run_01J…",
  "type": "test_outcome|test_change|behaviour_diff|probe_diff|api_diff|dep_diff|lock_drift|perf_diff|invariant|selection|network_attempt|subprocess_attempt|file_attempt|environment|static_change|policy_change|policy",
  "targets": [{ "file": "...", "symbol": "...", "test": "..." }],
  "summary": "parse_date('2026-03-29T02:30', tz='Europe/Berlin'): base → 2026-03-29T01:30Z, head → raises NonExistentTimeError",
  "data": { },                         // type-specific, schema'd
  "artifacts": [{ "path": "runs/<run>/obs/…", "sha256": "…", "media_type": "application/cbor" }],
  "stability": "stable|unstable|n/a",
  "origin": "introduced|pre-existing|n/a",
  "produced_by": { "component": "diff.behaviour", "version": "0.5.0" },
  "seq": 2210                          // seq of the evidence.created event
}
```
Evidence IDs are what verdicts cite. Validation rejects a verdict whose cited IDs do not exist,
do not relate to the claim's targets, or whose quoted text is not an exact substring of the
rendered evidence (§10.3).

### 6.1 Missing taxonomy
`no_covering_test` · `environment_failed` · `collection_error` · `timeout` ·
`unstable_observation` · `trace_unavailable` · `trace_truncated` · `subprocess_untraced` ·
`encoding_failed` · `network_not_enforced` · `perf_not_assessable` · `unsupported_project` ·
`verdict_rejected` · `claim_unverifiable` · `agent_failed` (an agent call ended without a usable
answer, G04) · `judge_is_author` (the reviewers are of the model family the change names as its
author, G08) · `memory_limit` (a side's run reached its sandbox memory limit, H01) ·
`run_incomplete` (a test run ended early or ran past its time limit, H06). Every item has a
reason, the affected targets, and where possible what would resolve it. For the status (F04), `no_covering_test`,
`unstable_observation`, `encoding_failed` and `trace_unavailable` leave the symbols they name
unverified (every changed symbol when they name none); the others are listed on the card and
counted, and what they stand for reaches the status through its own rule (a missing
environment, a job that did not complete, a test run that did not finish, an invariant not
evaluated).

## 7. Reproduction Card

| Section | Content |
|---|---|
| Claim | Atomic claims C1…Cn extracted from the PR (text, targets, kind: fix / feature / refactor / no-change / perf / deps / test / docs) |
| Conditions | base and head SHAs, interpreter, lock digests, selected tests, seeds, **isolation level achieved**, platform, Repro version, model IDs |
| Gap | Behaviour, outcome, API, dependency and perf differences, each with origin (introduced or pre-existing), stability, evidence ID and explained-by claim(s) |
| Verdicts | Per claim: `supported` / `contradicted` / `unverifiable`, cited evidence, rationale, agreement (panel) |
| Invariants | Result per declared invariant, with evidence |
| Missing | Missing items (§6.1) |
| Status | `VERIFIED` / `FLAGGED` / `UNVERIFIABLE` (§4.6) |
| Replay | Event-log digest and count, plus `repro replay <run-id>` |

As built (F05, `repro/card/`): the card stage is a run's last job, after every other however it
ended. It waits for its first event to be stored (the single writer commits in order, so every
earlier event is stored too), reads the run's log back, and assembles the card from the events
before `card.published` alone: F04's status and reasons, the conditions (head's interpreter and
lock, else base's; the lower isolation level; `network: enforced` only if every side's sandbox
enforced it; the seeds from the tests job's `environment` evidence; null for what the run did
not reach), the claims and verdicts, the gap with each item's resolution, the invariants and the
missing items. `replay.event_log_sha256` digests those events as canonical JSON lines, and
`generated_at` is the last one's time. The card is canonical JSON (sorted keys, no whitespace,
UTF-8); `card.published` carries it with its SHA-256 and counts, the `cards` table keeps it, and
`run.finished` takes its status. `repro export RUN|LOG --format md|json` assembles it again and
refuses one that differs from what the run published; `repro replay` reports the same check.
The cockpit opens it from a header button, in the drawer.

Exports (H04, `repro.card.exports`; `repro export --format in-toto|sarif|junit`):
- **in-toto Statement v1** with Repro's predicate (ADR-0012; described at
  `docs/attestation/reproduction-card/v0.md`, schema in `packages/events/schema/attestation`).
  The subject is the head commit (`gitCommit`). The predicate holds the card itself and its
  digest, the cited evidence as digested ResourceDescriptors, the event log's digest, and copies
  of the status, isolation level and network. `card_from_attestation` takes the card back out,
  checking each digest and copy: the round trip is exact. Unsigned; CI signs (I03).
- **SARIF 2.1.0**: a result per gap item, failed invariant, judged claim and missing item.
  `kind` is `fail` (level error or warning), `review`, `open` (what could not be decided),
  `informational` or `pass`, with level `none` beside any kind but `fail` (§3.27.10).
  Locations are the files the evidence names.
- **JUnit XML** (Apache Ant's format): one test case per claim and one for the status.

Each says what the card says, no more: the attestation records what was run and observed. It
does not certify correctness, and LLM verdicts are labelled as judgements. Markdown (PR comment
and job summary) and canonical JSON are F05's.

## 8. Isolation and sandbox — see ADR-0004 and THREAT_MODEL.md

| Level | Mechanism | Windows dev box | Linux / CI |
|---|---|---|---|
| L0 | separate process per side, minimal env, fixed seeds | ✓ | ✓ |
| L1 detect | in-process socket guard + `sys.addaudithook` → evidence of network, subprocess and file attempts | ✓ | ✓ |
| L2 contain | Windows Job Objects (kill-on-close, memory, CPU, process count) or POSIX process groups + rlimits, plus wall-clock timeouts | ✓ | ✓ |
| L3 enforce | Linux container: `--network none`, read-only root + tmpfs `/tmp` (mounted `exec`; A08 found `noexec` breaks real suites), `--cap-drop ALL`, `no-new-privileges`, non-root, pids/memory/cpu limits, snapshot copied into a volume (no bind-mounted live worktree, no `.git`) | Docker Desktop (WSL2) | Docker / Podman |
| L4 harden | gVisor `runsc` as Docker's runtime (`docker_runtime = "runsc"`, H03): the target's containers run on a kernel in user space | — | CI job `gvisor` |
| L5 microVM | Kata Containers as Docker's runtime (`docker_runtime = "kata"`, K10): each container in a VM of its own, with its own kernel (Cloud Hypervisor; the snapshot's volume shared over virtio-fs). The level is probed, never named: a container of the runtime whose kernel is not the engine's | WSL2 with nested virtualization (`scripts/kata/setup.sh`) | a host with KVM (`scripts/kata/setup.sh`) |
| L5 microVM (E2B) | `sandbox = "e2b"` (K09): each side in a Firecracker microVM of a self-hosted E2B (Embed), the target's code as a user without sudo, the network closed by the VM's own kernel firewall and checked from inside | WSL2 (WSL 3.0.3 or newer: kernel 6.8+) | CI job `e2b` |

Rules:
- L1 is **detection, not a boundary**. Python's audit-hook docs say hooks are not a sandbox, and
  in-process socket patches are trivially bypassed (research/03 §6.1).
- Network can be reported as *enforced* only at L3 or above. Otherwise the card says
  `network: detected-only`.
- The provision pass is the only pass with egress, limited to the package index. As built
  (D03): the dependencies are installed from wheels only (`--no-build`) into an environment that
  holds nothing of the project, cached by the digest of what it holds and copied for each side;
  then the project is built offline in the sandbox. Requirements naming anything but the index
  are refused beforehand.
- uv uses `--link-mode copy`. uv cannot use a read-only cache (D03 measured it), so no cache is
  shared with target code: each side's uv cache is its own and thrown away (research/03 §7).
- Target code never runs in a linked worktree on the host. A linked worktree shares the host
  repo's `.git` config and hooks.
- **L0–L2 as built (D01, `repro.sandbox`):** a minimal environment with a fresh home and
  temporary directory; Repro's audit hook first on `PYTHONPATH`, in every Python process of the
  command; on Windows a Job Object (the command created suspended, assigned, resumed), on POSIX
  a new session with resource limits set by a launcher before `exec`; a wall-clock timeout that
  kills the tree, and processes left behind killed and reported. `sandbox.ready` states the
  level reached and `network: detected-only`.
- **Another machine's engine (K05, `docker_context`).** Every container command goes to the
  Docker context named. Since nothing is bind-mounted from the host (H06), a team's engine
  over SSH runs the same sandbox. Isolation is that engine's: L3, L4 with gVisor there, or L5
  with Kata.
- **An image's own environment (J04, `sandbox_image`).** The containers run in that image, at
  L3 as above, and nothing is installed: the tests run with the image's interpreter (its named
  conda environment's, as SWE-bench-style task images have, else `python3`), with that
  environment's programs first on `PATH`. The snapshot goes first on the import path, after
  Repro's hook. Before any test, each of the snapshot's top-level packages must import from the
  snapshot; if one fails to import, or would come from a copy the image holds installed, the
  side has no environment and the card is UNVERIFIABLE. Repro never runs the image's copy in
  place of the change. Code that needs a build step (a compiled extension) does not import
  from a bare snapshot, so it is reported this way.
  The image itself is the operator's choice and is trusted as such: its interpreter is what
  runs Repro's hook, so an image built to deceive could. Pinning by digest fixes which image
  that is, and the card names it.
- **L3 as built (D02, `repro.sandbox.DockerSandbox`):** the commit is streamed from its tree
  object into a volume (modes and links exact on any host), unpacked by a no-network helper and
  owned by uid 1000. Each command runs in a fresh container with the hardening above, pids,
  memory (no swap) and CPU limits, and only Repro's audit hook mounted (read-only). Outputs are
  read back with `docker cp`. `repro doctor` reports the level runs will reach.
- **L5 as built (K10, ADR-0018 option C):** the same sandbox, with `docker_runtime = "kata"`.
  Docker runs each container through Kata's shim, in a microVM of Cloud Hypervisor with a
  kernel of its own; the volumes reach it over virtio-fs, so nothing else changes. When it
  starts, the sandbox runs `uname -r` in a container of the runtime, with no network, and
  compares it with `docker info`'s kernel: only a different kernel is L5, whatever the
  runtime's name. Kata's VM does not apply Docker's pids limit (`pids.max` reads `max` in it),
  so at L5 the process limit is also uid 1000's `nproc` (`--ulimit`), which counts only the
  container's processes in a kernel of its own; on a shared kernel it would count the host's
  processes of that uid too. `scripts/kata/setup.sh` sets up such a host: Docker Engine and
  Kata 4.2.0, pinned and checked, and a self-test.
- **L5 in E2B (K09, ADR-0018 option B, `repro.sandbox.E2BSandbox`):** a `RemoteSandbox` as
  the Docker sandbox is, so the runners treat both alike. Each side is a microVM from a
  template of Repro's image recipe (built once per E2B). Its dependencies install from
  wheels before the **seal**:
  - root adds an nftables rule to the VM's own kernel that rejects every packet `repro`
    (uid 1001, no sudo) sends, but to loopback;
  - E2B is asked to deny every address too;
  - a probe as `repro` must fail to reach the index by name and two addresses by number.

  Only then may anything of the target's run, as `repro`, with its process limit. K09
  measured E2B's own firewall letting TCP to numbered addresses through, so the guest's
  rule is the enforcement and E2B's the second layer. E2B is reached as its SDK says
  (`E2B_API_KEY`, `E2B_API_URL`); its SDK is the `e2b` extra.

## 9. Static analysis — see ADR-0006

- **Symbols:** `ast` with `lineno`/`end_lineno`. Symbol ID = `module:qualname#ordinal`, the
  module named by walking up through `__init__.py` directories. A symbol changed when its own
  code changed: its AST without positions, nested definitions reduced to markers, so comments
  and reformatting are not changes. A class body change belongs to the class, and module code
  outside definitions to the symbol `<module>`. Repeated names are aligned by own code. Rename
  and move matching across sides uses AST similarity when qualnames differ. Files that cannot
  be parsed are reported, never guessed (C06).
- **Import graph:** `grimp` (BSD-2, Rust core, handles lazy imports) for the regular packages,
  run as `python -I -S` in its own process and handed each package's directory, so no target
  name is resolved through `sys.path` (THREAT_MODEL T19). Modules outside packages (tests,
  scripts) are read with `ast` and resolved against the same names. It is module-level only.
  Symbol-level reverse edges come from the dynamic per-test map, with optional static call edges
  later (C07).
- **Public API:** `griffe` with `allow_inspection=False`, so target modules are never imported
  on the host. "Public" follows griffe's conventions, and false positives are possible and
  documented. As built (E05, `analysis/api.py`): the project's packages (import-root
  directories with `__init__.py`, test packages left out), their files written from git to a
  scratch tree by module name; `find_breaking_changes` plus a text comparison of return and
  attribute annotations, which griffe 2.3.2 leaves as TODOs; explanations are Repro's own (no
  host paths). `diff.api` is `warn`.
- **Blast radius:** changed symbols ∪ their reverse dependents (dynamic map ∪ grimp
  downstream modules), with distance kept for UI weighting. Statically, it walks the imports of
  both sides from the changed symbols' modules; an affected module is its `<module>` symbol.
- **JavaScript and TypeScript** (K08, `analysis/javascript.py`): symbols and imports by
  scanners, not a parser. The public API (K11, `analysis/jsapi.py`) is read by Repro's own
  pinned TypeScript compiler (`analysis/typescript.py`), never the target's:
  - `typescript` 6.0.3, fetched once from the npm registry, checked against its sha512 and
    unpacked with no package manager;
  - run by Repro's worker on the host's Node, which parses and type-checks the target's files
    and runs none of them.
  - **Symbols:** K06's function scanner, run on a source whose comments and strings are
    blanked. A function is `<path>:<name>#0`, by its name in its file; the path is
    percent-encoded where `SymbolId` allows no character.
  - **Imports:** the relative specifiers of `import`, `export … from`, `import()` and
    `require()`, resolved as Node and bundlers resolve them. A package import is not an edge.
  - **Blast radius:** Python's.

## 10. Agents — see ADR-0007

### 10.1 Roles
| Role | Default model (env-configurable) | Job |
|---|---|---|
| Reader | `claude-haiku-5-5` (fallback `claude-sonnet-5-5`) | Split the PR text into atomic claims with targets and kind |
| Reviewer ×n | `claude-opus-5-5`, `effort=high` set explicitly | One per claim: supported / contradicted / unverifiable, citing evidence |
| Gap reviewer | `claude-opus-5-5` | Map each gap item to the claims that explain it, or to "unexplained" |
| Probe writer | `claude-sonnet-5-5` | Write differential probes for changed functions without seeing the claims (§4.4) |
| QA | `claude-opus-5-5` | Check consistency of the whole card; finds contradictions between verdicts |

The judge model should differ from the model that authored the PR when that is known, because
self-preference bias is documented (research/04 §2.3).

As built (G04, `agents/setup.py`, `agents/reader.py`, `agents/stages.py`): the `agents` setting
chooses the backend (`off` by default: a run is deterministic only and reaches no model;
`claude`; `groq`, every role on `openai/gpt-oss-120b`, ADR-0016; or any `openai`-compatible
endpoint), `agent_model` puts every role on one model, `agent_mode` wraps it in cassettes, and
`agent_budget_usd` (2.0) bounds a run's agent spending. The reader is the `claims` job in the
read stage, running beside the build: it is given the changed files (`git diff`) and the PR's
title and description (`--pr-title`, `--pr-body`, `--pr-file`) or else its commit messages,
each in an untrusted block and capped at 4,000 characters; it answers in a strict schema whose
files are an enum of the changed files; the claims are checked again (empty text or an unknown
kind dropped, files outside the change dropped, duplicates merged, at most 8), numbered `C1`…
and emitted as `claim.extracted`. A reader that ends without an answer fails the job: an
`agent_failed` missing item.

### 10.2 Runtime lockdown (Claude Agent SDK; research/04 §1.3)
`tools=[]` (no built-in tools). The only tools are a call's own in-process MCP tools,
`mcp__repro__<name>`, and, when the call asks for structured output, the CLI's
`StructuredOutput` tool, through which the answer arrives. `permission_mode="dontAsk"` plus a
`PreToolUse` hook that denies every other tool, a Repro tool the call was not given included.
`strict_mcp_config`, no plugins, no skills. `setting_sources=[]`,
`CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, a per-call
`CLAUDE_CONFIG_DIR`, and `verbatim_prompts=True`. `cwd` is an **empty per-call scratch
directory, never a worktree**, so the PR cannot inject `CLAUDE.md`, hooks or skills. The system
prompt is passed as a file because of the Windows command-line length limit. `max_turns`,
`max_budget_usd` and a wall-clock timeout are set on every call; subagent spawning is held to
its floor (depth 1, one at a time) and no Agent tool is in context (G01, `repro.agents.lockdown`).
The tests run the bundled CLI against a local stand-in for the Messages API, and each planted
file is first shown to load on an unlocked CLI (`test_agents_cli.py`).

PR text is wrapped in delimited `<untrusted_pr_description>` blocks and described to the model
as *claims to check, not instructions*.

As built (G03, `agents/tools.py`, `agents/evidence.py`, `agents/sources.py`,
`agents/untrusted.py`): the tools are `list_evidence` (optionally about a file, symbol or
test), `get_evidence`, `read_snippet` (80 lines at most) and `get_diff` (160 lines at most).
Evidence comes from the run's own log, rendered per type with only the fields that describe
the finding, in content order, and without what varies between runs of the same change
(timings, process IDs, digests). Files and diffs are read from the commits' git objects, never
the snapshots the target's tests ran in, by paths checked before git sees them, with
`--no-ext-diff --no-textconv`. The target's text sits in `<untrusted_content kind=…>` blocks
whose delimiters it cannot write, made portable (LF, `~` for the home directory, `/` in
pytest's file locations).

### 10.3 Evidence-constrained verdicts
- The reviewer output schema is generated **per claim**. `cited_evidence` is an array with
  `minItems: 1` whose items are an **enum of the evidence IDs relevant to that claim**, so the
  model cannot cite a nonexistent ID. Schemas are draft-07 and `$ref`-free, because the Agent
  SDK validates draft-07 and rejects newer drafts (research/04 §1.5).
- Post-validation in `agents/validate.py` checks three things. Every ID exists and relates to
  the claim's targets. Every quote is an exact substring of the rendered evidence. The verdict
  is consistent with the deterministic evidence: for example, `supported` while a stable
  contradicting diff on the claim's own target is cited is rejected for QA.
- When validation fails or retries are exhausted, the engine emits `verdict.rejected` and a
  `verdict_rejected` missing item. It never falls back to an uncited verdict.
- As built (G05, `agents/reviewer.py`, `agents/validate.py`, the `review` job in
  `agents/stages.py`): after every other job, one reviewer per claim (prompt `reviewer/1`,
  effort high) is given its claim and the PR's other claims in untrusted blocks, the evidence
  about the claim's files rendered in full (at most 10 items; G03's tools offered only when
  more exist), and those files' diffs. Its schema's `cited_evidence` has `minItems: 1` and an
  enum of that evidence's IDs; Groq's strict mode accepts it (measured). A claim with more
  than 10 items is shown what differs first: each group of alike findings (same type, kind of
  change and first target) once in full with its other IDs, and a count of what says nothing
  changed (tests that passed on both sides, unchanged timings), which `list_evidence` lists
  last. Past 40 IDs the schema takes an ID as a string instead of an enum. On a real
  repository the enum was most of each request (`docs/paper/LOG.md`), and the
  check refuses an ID that is not the claim's evidence either way. The answer is checked
  for shape, citations (existing, about the claim), quotes (exact substrings of the evidence's
  rendering) and consistency: `supported` resting only on unstable evidence, or a claim that
  nothing changes supported by a stable introduced difference while no other claim describes
  a change, is rejected. A rejected verdict gets one more attempt, told why; then
  `verdict.rejected` and `verdict_rejected`. A claim with no evidence about it is not
  reviewed: `claim_unverifiable`. A reviewer that ends without an answer: `agent_failed`. A
  claim that "nothing else" changes is judged beside the PR's other claims, which say what
  "else" means; whether every difference is accounted for stays with the gap (F04, G06).
- As built (G06, `agents/gap.py`, the `gap` job after `review`): the differences that would
  flag only for want of an explanation (`warn` checks no supported verdict cites, none
  contradicted, none a problem on its own) go to one gap reviewer (prompt `gap/1`) with the
  supported claims that describe a change (not `no-change`, `refactor`). Its mapping is checked
  like a verdict (given items only, once each; given claims only, about the item's files;
  exact quotes); what survives becomes, per claim, a supporting `verdict.issued` citing the
  differences it explains, so F04's rules count them explained. A mapping that fails leaves
  its difference unexplained, and it flags. With nothing to explain, or no claim that could,
  no call is made. Evidence relevance (G03's rule) also follows test selection from G06 on: a test is
  about the files of the changed symbols it was selected for (`test.selected`), so a new or
  newly passing test is evidence about the code it exercises.

### 10.4 Bias controls
Evidence is rendered in a fixed order, with sides labelled neutrally (A/B plus a legend). As
built (G03): the order is by content (type, targets, summary), since IDs follow creation time,
which varies with how a run's jobs interleave; the sides keep the engine's names, `base` and
`head`, which its evidence summaries use, and position bias is left to the order-swap pass. Prompts are reference-guided: the deterministic diff is given and the model judges
only whether the claim's wording is supported. PR text fed to judges is capped and judged per
atomic claim. For `contradicted` verdicts, a second pass with the order swapped must agree, or
the verdict degrades to `unverifiable`. An optional 2-of-3 panel uses different models or
effort levels. Agreement is logged per run.

As built (G08, `agents/bias.py`, `agents/qa.py`, the review and `qa` jobs):

- **Order swap.** A verdict is judged again with three things reversed: the evidence given in
  full, the other claims, and the evidence-ID enum. Only `contradicted` verdicts are judged
  again by default (`agent_order_swap`; `all` judges every verdict again, which is how the flip
  rate is measured; `off` judges none).
- **When the two orders disagree.** The verdict is issued as `unverifiable`, with three
  things attached:
  - the first judgement's citations;
  - a rationale that says what the other order answered;
  - `verdict.rejected` with `order_swap_disagreement`.
- **Agreement.** Every verdict's `agreement` records `{runs, agreeing}`: how many judgements it
  rests on, and how many gave the issued verdict.
- **Panel.** `agent_panel` names three models. Each judges the claim, the second with the
  order reversed. A verdict stands when two give it, and is `unverifiable` otherwise.
- **Judge ≠ author.** The `claims` job reads co-author trailers and "generated with" lines in
  the commits and the PR text. When they name a model family and every reviewer belongs to it,
  the card lists `judge_is_author`. Families are read from model names only, never from
  people's names or addresses.
- **QA.** The `qa` job runs after `gap`. It reads every claim with its verdicts, their
  rationales and a line per cited evidence item, and names verdicts that cannot hold together.
  A checked inconsistency withholds each supported claim it names with an `unverifiable`
  verdict. QA can make a card UNVERIFIABLE, never FLAGGED.

### 10.5 Backend abstraction and CI determinism
`agents/backend.py` defines an `LLMBackend` protocol: `run(request, on_progress)` gives an
`AgentResult` whose outcome is `ok`, `max_turns`, `max_budget`, `timeout`, `invalid_output` or
`error`, and never raises for what the model does. The default implementation is the Agent
SDK (`agents/sdk.py`). A direct Anthropic Messages API backend, which gives constrained
decoding and no per-agent CLI subprocess, is an open option (ADR-0007). In CI, calls are served
from **cassettes** keyed by `(prompt version, model, input digest)`. A missing cassette fails the
test and never silently calls the API.

As built (G02, `agents/cassette.py`): the digest is the SHA-256 of what the model is given (role,
prompts, tools, output schema, effort; not the budget, which a run's ledger may lower) with its
run-specific IDs (`ev_`, `evt_`, `run_`) replaced by kind and first-seen order, so the same call
in another run has the same digest. A cassette holds the request, the transcript (progress and
Repro tool calls with their outputs, in order) and the result, as canonical, scrubbed JSON at
`<root>/<prompt>/<version>/<model>/<digest>.json`. Replay has no backend to call; it maps the
recording's IDs to the current run's by the same order, makes the recorded tool calls again and
fails as stale if one returns something else. `timeout` and `error` outcomes are never recorded.
Prompts are versioned files (`agents/prompts/<name>.md`, `version:` front matter) whose texts are
locked by SHA-256 per version (`lock.json`, only added to); a call names its prompt as
`<name>/<version>`. Costs come from the CLI's per-model `model_usage`, one `cost.estimate` per
model with `source` `live` or `cassette`; a run's `RunLedger` totals them and, with a limit,
reserves each call's dollar budget from what is left. A second backend (G10, ADR-0016,
`agents/openai_compat.py`) speaks to OpenAI-compatible APIs (Groq, local servers): Repro runs
the tool loop, offers only the call's own tools and refuses any other name, asks for a
structured answer alone with `response_format` `json_schema` (the API cannot combine it with
tools) and checks it against the schema, honours `retry-after`, and prices calls from a sourced
table (a model without a price gets no `cost.estimate`). Tests never reach a model: the conftest
points the API at a closed loopback port, and tests that start the CLI run behind a proxy that
fails them on any connection past loopback.

Concurrency defaults to `min(12, free RAM in GiB)`
(research/04 §1.10: about 1 GiB per SDK subprocess). The UI always shows 12 dispatch slots, with
queued agents shown as `waiting`. As built (G01, `agents/dispatch.py`, `agents/events.py`): an
agent takes the lowest free slot (`agent.spawned`), shows `waiting` only if the run's
concurrency is taken, then `thinking` and `tool_call` with `progress` the share of its turn
budget used, then `done` or `failed`, and `cost.estimate`; an agent queued behind twelve others
has no slot, and so no events, yet. Free memory is `ullAvailPhys` on Windows and `MemAvailable`
on Linux; where it cannot be read, one agent runs at a time.

## 11. Storage and transport

- **SQLite** at `~/.repro/repro.db` (`%USERPROFILE%\.repro` on Windows), opened with WAL,
  `synchronous=NORMAL`, `busy_timeout`, and `foreign_keys=ON`. The tables are `runs`,
  `events (PRIMARY KEY(run_id, seq), UNIQUE(id))`, `evidence`, `cards` and `artifacts`.
  **One writer task** batches events (N events or about 20–50 ms) into a `BEGIN IMMEDIATE`
  transaction, assigns `seq`, commits, and only then publishes to subscribers. Readers use
  separate read-only connections. `wal_checkpoint(TRUNCATE)` runs at run end.
- **The schema enforces the log's rules** (C02, `repro.store.schema`): triggers make `events`
  and `evidence` append-only and reject any event whose `seq` is not its run's next number;
  `PRIMARY KEY (run_id, seq)` rejects duplicates; an evidence row must belong to an
  `evidence.created` event. Migrations are numbered by `PRAGMA user_version`, one transaction
  each; a database from a newer Repro is refused. The writer fills `seq` into the envelope and,
  for `evidence.created`, into `payload.evidence`, and inserts the evidence row in the same
  transaction. `ts` is stamped where an event is built, before it is queued, so events that
  jobs emit at once can arrive out of order. The writer stores an event stamped before its
  run's latest with that latest `ts`, so `ts` never goes backwards within a run's `seq`.
  `seq` stays the only order (ADR-0002). The run registry (`runs`) holds `running`, then the
  `run.finished` outcome.
- **Leases** (schema version 2): the writer that registered a run renews its `heartbeat_at`
  every minute while the run is open. A writer that starts ends, as `failed`, every running
  run whose lease is more than ten minutes old: its process was killed, so it can never
  finish. No event is added and no card is made. The log stops where the process stopped, and
  replays as such.
- **Artifacts** live at `~/.repro/runs/<run_id>/`: observations, JUnit, logs, snapshots
  manifest and reproducers. Observations (E03) are deterministic CBOR (RFC 8949 §4.2.1,
  `repro.store.cbor`): `obs/A.cbor`, `obs/A2.cbor`, `obs/H.cbor`, and `obs/behaviour.cbor`, the
  comparison, which cites the three by path and SHA-256. Each artifact is content-addressed by SHA-256 in the `artifacts`
  table, written to a temporary file, hashed, flushed and renamed into place, and never changed.
  Paths are relative POSIX paths confined to the run's directory.
- **WebSocket** `GET /runs/{id}/events?from=<seq>` uses replay-then-live without gaps. The
  client's bounded queue is subscribed **first**. Then rows with `seq > from` are streamed in
  pages, the queue is drained with `seq ≤ last_sent` deduplicated, and a `caught_up` frame is
  sent before live events continue. Frames carry arrays of events batched every 16–33 ms. A
  client that falls behind is closed with a "resync" reason and reconnects with `?from=`. The
  writer never waits for clients.
- **Frames and closes (C04):** `{"type": "events", "events": [...]}` carries stored bodies
  verbatim; `{"type": "caught_up", "seq": n}` ends the replay. The server closes with 1000
  "finished" after `run.finished`, 4000 "resync" when a client falls behind, 4400 for a bad
  request and 4404 for an unknown run. A handshake whose `Origin` is not allow-listed is refused
  (HTTP 403), and every request must name a local `Host` (THREAT_MODEL T16).
- **REST:** `/runs`, `/runs/{id}`, `/runs/{id}/evidence/{ev}`, `/runs/{id}/card`, and
  `/runs/{id}/artifacts/{sha}` (served only while the file matches its digest). Read-only.
- **The session token (H03, THREAT_MODEL T13):** `repro serve`, `run` and `replay` listen on
  loopback only, and each server makes a token of its own. Every request for the engine's data
  carries it, as `Authorization: Bearer <token>`, or on the event stream as `?token=`; without
  it the answer is 401, or a refused handshake (HTTP 403). The cockpit's files need none. The
  printed URL carries the token in its fragment (`#token=…`), which the cockpit keeps in
  `sessionStorage` and removes from the address bar (`apps/web/src/data/session.ts`); a page
  without it says to open the printed URL. There is no access log.
- **The cockpit's client (C08):** the page's query picks the source: `?run=<id>` follows a run
  live, `?run=<id>&replay` replays it from the start, `?log=` loads a fixture or a same-origin
  log. Frames go to the cockpit's store, which ignores duplicates and stops at a gap; the
  client then reconnects with `?from=` its last `seq` (as after a 4000 "resync"). Lost
  connections are retried with backoff; 4400, 4404 and refused handshakes are reported. When a
  live stream ends, the clock waits at the end of the data, as a replay does, so a live view
  and its replay end in the same state. `repro run`, `replay` and `serve` serve the built
  cockpit at `/`, after the API's routes.

## 12. Configuration

| Layer | Where | Examples |
|---|---|---|
| Engine settings | env (`REPRO_*`) and `~/.repro/config.toml` | data dir, concurrency, sandbox backend, models, budgets |
| Repository policy | `repro.yml` in the target repo (read from **base**, never head) | invariants, thresholds, protected paths, claim requirements |
| Run options | CLI flags | refs, PR text file, deterministic-only, export formats |

Engine settings resolve in this order: command-line flag, `REPRO_<NAME>` variable, the TOML
file (`REPRO_CONFIG`, else `config.toml` in the data directory), default. Unknown keys and invalid
values are errors naming their source (C01; `engine/README.md` lists the settings).

Policy is read from the base ref because a PR must not be able to weaken the rules it is
judged by. If head changes `repro.yml`, that becomes a gap item and is flagged by default.

As built (F01, `repro/policy/`): the file is validated against `repro-yml.schema.json` (JSON
Schema 2020-12, PRODUCT.md's MVP set plus an optional `version: 1`), parsed with PyYAML's safe
loader (one document, 64 KiB at most, no anchors or aliases, no duplicate keys), each error
naming its line, column and path. No file: the defaults, no invariant. An invalid file: no
policy, and an `unsupported_project` item listing the issues. Head's addition, removal or edit
is `policy_change` evidence, with the ways it would weaken base's policy.

As built (F02, `policy/invariants.py`, the `invariants` job in the check stage): each invariant
base enables gets `invariant` evidence and an `invariant.result` (`ok` / `fail` / `warn` for
pass / fail / not evaluated), citing the `api_diff` or `dep_diff` evidence it rests on. The job
comes after `api` and `deps` without needing them to succeed, so an invariant whose evidence
could not be produced is `not_evaluated`, never skipped or passed. `forbidden_paths` patterns
are paths (`*`, `**`, `?`; a pattern matching a directory forbids what is under it); one that
could never match a repository path is a policy error.

As built (F03): `no_network` fails on any network attempt head's test runs made past this
machine (the sandbox's `network_attempt` evidence; loopback, unspecified addresses,
`localhost` and Unix sockets are local), and its pass states head's network, `enforced` (L3)
or `detected-only`; a `detected-only` pass adds a `network_not_enforced` item. `perf_budget`
fails a timed test whose D07 verdict is `regression` with its interval's low end past
`1 + max_regression`; past the budget without that verdict is not evaluated.

## 13. Web cockpit — see ADR-0008, ADR-0009, ADR-0011, DESIGN.md

```
WS / JSONL ─► EventStore (append-only, columnar typed arrays + payload array; validates v)
                 ├─► FoldedProjections (pure reducers; snapshot every N events) ─► signals ─► shell DOM + a11y DOM
                 │                                                            └─► panel view-models (dirty flags)
                 └─► WindowIndex (binary search on ts) ─► TransientProjections f(event, t, speed) ─► GPU instance buffers
Clock: t = live ? now − t0 : replayT (×0.5/1/4/16, scrub, pause) ──► frame(t) renders everything
Workers: layout.worker (d3-force, seeded, cached by graph hash) · atlas.worker (OffscreenCanvas 2D → ImageBitmap)
```

- **EventStore (B02).** Columns (`seq`, presentation time, kind, stage, status) are typed
  arrays next to the parsed events. Presentation time is the running maximum of `ts`, so a
  backwards timestamp never breaks time queries. The store takes only a gap-free sequence: a
  duplicate `seq` is ignored, and a gap stops ingestion so the transport resyncs with `?from=`.
  Unknown kinds, and known kinds with a newer `pv`, are stored but not interpreted.
- **Two kinds of UI state.** *Folded* state (counters, index, agents, grids, log) comes from pure
  reducers with snapshots for fast seek. *Transient* visuals (strands, ring snap) are
  pure functions of the events whose visual window contains `t`. Scrubbing never rebuilds
  accumulated animation state.
- **Stage:** three.js `WebGLRenderer` with `RawShaderMaterial`, instanced layers (edges, nodes,
  markers, strand bullets, dim strands, lines outside the wheel, ring cards, ring ticks, focus
  highlights), about one draw call each. GPU code sits
  behind a `Layer` interface (about 200 lines of glue), so the renderer can be swapped if the
  budget demands it.
- **Indexed state (B05, B08, B09).** Besides folded projections, append-only indexes record
  the store index of each entry, so views ask "as of cursor k" without folding: `GraphIndex`
  (nodes, edges), `TestIndex` (test runs), `ObservationIndex` (trace samples per test × symbol)
  and `ChangeIndex` (changed symbols, selections, and the routes between them). A selected test
  routes to a changed symbol if it was selected for it or for a caller within 3 `calls` hops, or,
  for a module-level change, if its module imports the changed module. Each selection is routed
  with the graph and changes that precede it in the log, so routes are pure in the cursor.
- **Panels:** Canvas 2D, written as pure `draw(ctx, view, size, t)` functions so they can move to
  an OffscreenCanvas worker if profiling shows main-thread contention. A `PanelHost` redraws a
  panel only when its `version(t, cursor, motion)` or the motion settings change, clearing the
  whole backing store first. Equal versions must mean equal drawings; a B10 property test checks
  this frame by frame, which is what makes playing equal to scrubbing.
- **Focus and accessibility (B11).** One focus (claim, file, symbol, check or agent) drives the
  stage highlight and ring pin (`StageFrame.focus`), the panels' outlines, the agent workspace
  and the evidence drawer (`drawerView`, a pure function of focus and cursor). Panels expose
  `targets` (rows and cells with their rectangles) from the same geometry they draw with, for
  hit tests and the keyboard mirror. Stage clicks are hit-tested against the ring's closed-form
  rotation and the web layout.
- **Motion (B10, ADR-0011).** The frame loop passes every layer and panel the replay speed and
  reduced motion. Visual durations are wall-clock, so the stage is a pure function of (events,
  t, motion). A `FlashGovernor` decides, in log order, which bunches of strands are drawn and
  which edges and markers flare, keeping the bright area that rises in any 333 ms under its cap.
- **View and look (B15).** The stage's ring geometry comes from the layout and the viewer's zoom
  (`viewRing`), so every layer, the governor and the hit tests follow the view. The viewer's look
  (`Look`: per-part width and brightness multiples) reaches layers through `StageFrame.look`;
  layers do not read it, the host applies it as shader uniforms, and the governor reads the
  widths it counts. It is kept per viewer in `localStorage`, always read through `clampLook`.
- **Shell:** Preact 11 + signals for header, controls, drawer and the accessible parallel DOM.
- **Determinism:** no `Date.now()` or `Math.random()` in projections. Per-event visual jitter
  comes from `hash32(event.id)`. Layout initial positions are seeded from node-ID hashes (no
  trig), so d3-force output is reproducible.
- **Accessibility:** global pause (WCAG 2.2.2), a `role="log"` mirror of the investigation feed
  with throttled announcements, semantic tables for card and index, keyboard focus, and
  `prefers-reduced-motion` support. A flash governor and wall-clock-clamped motion at high replay
  speeds address WCAG 2.3.1 (ADR-0011).

## 14. Cross-cutting concerns

- **Logging:** structured JSON logs for engine internals. User-visible activity is `log.line`
  events. Secrets are never logged.
- **Errors:** every stage failure becomes events (`status: fail`) plus missing items. A run
  always ends with `run.finished` and, where possible, a card. In the orchestrator (C03) a failed
  or timed-out job emits a `log.line` with status `fail` and a `missing.recorded` item (a timeout's
  reason is `timeout`); a job whose dependency (`needs`) did not succeed is skipped and records
  its missing item, while a job that only comes `after` others (F02) runs once they end, however
  they end, and says itself what it could not evaluate; `run.finished` is emitted in a `finally`. Jobs run on daemon threads; timeouts and
  cancellation are cooperative inside Python and forceful for subprocesses (the process tree is
  killed).
- **Windows specifics:** `git -c core.longpaths=true` per command (the user's global config is
  never modified). Run roots are short (`%LOCALAPPDATA%\repro\r\<id8>`). Cleanup goes Job
  Object kill, then `rmtree(onexc=…)` with retry, then `git worktree prune`. Snapshots are
  written from the commit's tree object (`git ls-tree` and one `git cat-file --batch`), not
  `git archive`, which applies the archived tree's `export-ignore` and `export-subst`; files are
  the committed bytes, through extended-length paths on Windows (C05). Opening the
  database retries a transient "disk I/O error" for up to 5 s: Windows releases a killed
  process's file locks with a delay (C02). All paths use
  `pathlib`, and tests cover backslashes and long paths.
- **Versions:** toolchain versions are pinned in lockfiles. The verified-current versions as
  of 2026-10-13 are listed in [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md#toolchain-baseline).

## 15. Key technical risks

| Risk | Impact | Mitigation | Phase |
|---|---|---|---|
| `sys.monitoring` callback overhead unknown for arg/return capture | Slow runs, truncated traces | Measure first. Budgets, target-only local events, DISABLE elsewhere | A08, E01 |
| Trace noise (timestamps, IDs, ordering) makes the Gap panel noisy | Users switch it off | Structural encoder, A/A′ masking, order-only class, reruns | E02–E04 |
| Installing arbitrary repos twice is slow or fails | UNVERIFIABLE rate high | Env cache keyed by lock digest, explicit `environment_failed`, SWE-bench-style prebuilt images for evaluation | D03, J01 |
| Docker unavailable or slow on Windows | Weaker isolation | Honest `detected-only` reporting. Copy snapshots into volumes rather than bind mounts | D01–D02 |
| LLM verdict errors or bias | Wrong card | Evidence-enum schemas, quote validation, order swap, panel, deterministic status rules | G05–G07 |
| Platform vendors add base-vs-head diffing | Positioning erodes | Compete on function-level evidence quality, claim/missing semantics, local-first replay. Publish the benchmark | J |

## 16. Decision index

| ADR | Title | Status |
|---|---|---|
| [0001](adr/0001-record-architecture-decisions.md) | Record architecture decisions | accepted |
| [0002](adr/0002-event-log-source-of-truth.md) | The event log is the source of truth | accepted |
| [0003](adr/0003-event-envelope-versioning.md) | Additive envelope fields for versioning and causation | accepted |
| [0004](adr/0004-layered-isolation-docker-default.md) | Layered isolation with Docker as the default enforcing sandbox | accepted |
| [0005](adr/0005-behaviour-tracing-sys-monitoring.md) | Own `sys.monitoring` tracer, canonical encoder and selection map | accepted |
| [0006](adr/0006-static-analysis-reuse.md) | Reuse `ast`, grimp and griffe for static analysis | accepted |
| [0007](adr/0007-agent-runtime-lockdown.md) | Locked-down agent runtime behind an `LLMBackend` interface | accepted |
| [0008](adr/0008-stage-renderer-threejs.md) | Stage renderer: three.js WebGLRenderer with custom instanced layers | accepted |
| [0009](adr/0009-web-tooling.md) | Web tooling: Biome, Vitest, Playwright | accepted |
| [0010](adr/0010-evaluation-methodology.md) | Evaluation on fresh SWE-format tasks against execution-aware baselines | accepted |
| [0011](adr/0011-motion-safety.md) | Wall-clock-clamped motion and a flash governor | accepted |
| [0012](adr/0012-card-as-attestation.md) | Export the card as an in-toto attestation | accepted |
| [0013](adr/0013-python-quality-tooling.md) | Python quality tooling: Ruff and mypy (strict) | accepted |
| [0014](adr/0014-in-house-schema-codegen.md) | In-house code generation from the event schema | accepted |
| [0015](adr/0015-check-model.md) | The check model: what CHECKS, FLAGGED and the index count | accepted |
