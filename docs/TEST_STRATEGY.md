# Repro — Test Strategy

Repro asks people to trust its verdicts, so its own test suite has to show that the verdicts
are correct, deterministic and safe to produce. This document defines the test layers, what
each one protects, where it runs, and the fixtures it relies on.

## 1. Principles

1. **Determinism is tested, not assumed.** The same inputs give the same normalised event log
   (IDs and timestamps stripped). Golden-log tests enforce this.
2. **Every verdict path has a fixture.** For each way a change can be wrong (test flip, silent
   behaviour change, API break, new dependency, network access, perf regression, flaky test,
   false claim, unclaimed change) there is a tiny target repo that exhibits it, and a test that
   asserts the resulting evidence and status.
3. **The UI is tested as a function of events.** Projections are pure, so they are unit tested
   by folding event logs. Rendering is tested by visual snapshots at frozen replay times.
4. **LLM behaviour is measured, not unit-tested.** Deterministic CI uses recorded responses.
   Real model behaviour is measured by an eval suite with tracked metrics.
5. **Isolation is attacked.** Sandbox tests deliberately try to escape and must be blocked or
   detected.

## 2. Layers

| # | Layer | Tooling | Scope | Runs on |
|---|---|---|---|---|
| 1 | Static | Ruff, type checker (strict), Biome, `tsc --noEmit` | All code | Every PR |
| 2 | Schema | JSON Schema meta-validation, codegen drift check, cross-language round trip | `packages/events` | Every PR |
| 3 | Unit | pytest, Vitest | Pure logic: diff parsing, graph, blast radius, canonicalisation, diffing, status rules, projections | Every PR |
| 4 | Property-based | Hypothesis, fast-check | Canonical hashing is stable and deterministic. Projections: `fold(all) == fold(snapshot) + fold(rest)`. Diff symmetry | Every PR |
| 5 | Golden logs | pytest approval tests | Normalised event log of each fixture repo run | Every PR (fast fixtures), nightly (all) |
| 6 | Integration | pytest + scripted fixture repos | Engine stages against real git repos, real venvs, real pytest | Every PR (Ubuntu + Windows, small set), nightly (full) |
| 7 | End-to-end | pytest (engine) + Playwright (web) | `repro run` → WS → UI shows the expected state | Nightly, stage gates |
| 8 | Visual regression | Playwright screenshot comparison | Cockpit at frozen replay times on the reference fixture log | Every PR touching `apps/web` |
| 9 | Performance | Custom harness (`repro_bench.scale`, H02) + Playwright tracing | Frame-time budget, JS bundle size, engine wall-clock and cache use on three pinned reference repos | Bundle size on every PR; frame/engine perf nightly |
| 9a | Allocation | Vitest (heap after GC; V8 allocation counter) | 1,000 frames of the stress burst: flat heap; stage allocation per frame within budget | Every PR (Ubuntu + Windows) |
| 10 | Security | pytest sandbox suite, `test_secrets.py`, `test_threat_model.py`, pip-audit and `pnpm audit` | Network, filesystem, process, privilege, kernel and resource-exhaustion escape attempts, at L3, under gVisor (L4) and in Kata and E2B microVMs (L5); what leaves a run; each threat's tests (THREAT_MODEL §7); known vulnerabilities in the locked dependencies | Every PR (jobs `test`, `gvisor`, `kata`, `e2b`, `dependency audit`) |
| 11 | Agent evals | Eval harness over labelled PR set | Claim extraction, verdict accuracy, citation validity, cost | On agent changes (recorded), weekly live |
| 12 | Mutation | mutmut (engine core) | Measures how strong the suite is | Stage gates |

## 3. Fixture target repositories

Fixture repos are **generated at test time** by `engine/tests/fixtures/repo_builder.py` from
specs in `engine/tests/fixtures/specs.py`. They are never committed as nested `.git`
directories. Each spec is a tiny Python project on `main` (base) plus one or more head branches.
Specs are Python data, so a fixture's deliberately buggy code is never collected, linted or
type-checked as Repro's own code. Commits use fixed identities and dates, files are written
with LF, and global and system git config is ignored, so **commit SHAs are identical on every
OS**. A test pins them.

| Fixture | Head branch exhibits | Expected evidence | Expected status |
|---|---|---|---|
| `honest-fix` | Bug fix matching its claim | test flip fail→pass on claimed target only | VERIFIED |
| `silent-change` | Output change in an untested path | behaviour diff, no test outcome change | FLAGGED (unexplained) |
| `false-claim` | Claims "no behaviour change", changes behaviour | behaviour diff contradicting claim | FLAGGED |
| `api-break` | Removes a parameter of a public function | API diff | FLAGGED (`public_api_stable`) |
| `new-dep` | Adds a dependency | dependency diff | FLAGGED (`no_new_dependencies`) |
| `net-call` | Code under test opens a socket | network attempt recorded | FLAGGED (`no_network`) |
| `perf-regress` | Quadratic loop in hot path | perf diff over budget | FLAGGED (`perf_budget`) |
| `flaky` | Test with random outcome | observation marked `unstable` | not diffed; listed under *missing* |
| `noisy` | Refactors functions whose results or arguments carry time, random and set-order noise | every noisy call `unstable`, no behaviour difference (E03) | not diffed; listed under *missing* |
| `untested` | Change no test reaches | blast radius with no covering tests | UNVERIFIABLE / *missing* |
| `selection` | Changes whose affected tests a person can name, beside a test none affects | exactly the expected selected tests and reasons; no gaps (D05) | VERIFIED |
| `generated` | Adds dataclasses, a `NamedTuple`, an attrs-style class, an enum, an exception class and a docstring-only package; one dataclass nothing constructs | the classes the tests construct or import are covered; `Unused` alone is a `no_covering_test` item (D05 fix) | VERIFIED / *missing* |
| `breaks-one` | A change that breaks exactly one test | that test P2F, in Execution, Change routes and the index (Gate D) | FLAGGED |
| `forbidden-path` | Edits a protected path | invariant failure | FLAGGED (`forbidden_paths`) |
| `invariants-pass` | Every invariant enabled; a change that keeps them | every invariant passes (F02, F03) | VERIFIED |
| `test-tamper` | Deletes a failing assertion and adds `@pytest.mark.skip` to another test | `test_change` evidence. Base version of the edited test fails on head | FLAGGED |
| `probe-only` | Wrong output on inputs no existing test uses | `probe_diff` from generated probes | FLAGGED (Stage E06/G07) |
| `probe-semantic` | Wrong output only on realistic inputs (a timestamp with a half-hour offset); random ones find nothing | `probe_diff` from the probe writer's inputs (`found_by: agent`, G07) | FLAGGED with agents; VERIFIED without |
| `policy-weaken` | Head edits `repro.yml` to drop an invariant | policy change gap item. Policy still read from base | FLAGGED |
| `injection` | PR text, a code comment and a test's assertion message contain instructions aimed at the reviewer agents (G03: honest-fix's twin) | none. Tool outputs and verdicts unchanged vs the clean twin | same as clean twin |

Each fixture's expected outcome lives next to the specs as `<name>.toml` (stdlib `tomllib`).
From Stage D on, integration tests assert it end to end. Fixtures are added by the phase that
first needs them; `honest-fix` exists since A07, `selection` and `untested` since D05,
`flaky` and `test-tamper` since D06, `perf-regress` since D07, `breaks-one` since Gate D,
`noisy` since E03, `silent-change` and `false-claim` since E04, `api-break` and `new-dep` since
E05, `probe-only` since E06, `policy-weaken` since F01, `forbidden-path` and `invariants-pass` since
F02 (with `api-break` and `new-dep` given a base `repro.yml`), `net-call` since F03 (with
`perf-regress` given a 20 % `perf_budget`, and `invariants-pass` a test slow enough to time).
`generated` since the fix to D05's map of methods written for classes (after 1.6.0).
`net-call` connects to 192.0.2.1, in TEST-NET-1, which RFC 5737 reserves for documentation, so
nothing answers it even outside a sandbox. Its host is `bytes`, so the lookup does not load
Python's `idna` codec. That codec's first use took about 4 ms on Windows and put the test at
the timing floor (H02's follow-up). A fixture's `invariants` list
holds its `invariant.result` events as `<invariant>:<result>`. A fixture's `behaviour` list holds
the missing items the behaviour comparison adds (E03), and its evidence lists its
`behaviour_diff` items (E04), with `[reproducers]` where a test checks them; `test_run_stage.py`
expects them with the rest. Its `status` is the status with claims judged (Stage G); since F04,
`test_run_stage.py` also decides each fixture's status from its log without claims, and a
fixture whose differences only a claim explains (`honest-fix`, `selection`) says so with a
`deterministic_status` (FLAGGED); `probe-semantic`, added in G07, is the other way round:
VERIFIED without agents, FLAGGED once the probe writer's inputs find what random ones do not.

### Golden files and property tests

- `golden` fixture (root `conftest.py`): compares text with `<test dir>/golden/<name>` using LF
  line endings on every OS. `pytest --update-goldens` rewrites the files; review the diff before
  committing.
- Event logs are compared in normalised form (`repro-events normalise`). Ids, timestamps,
  durations and producer version are replaced deterministically, and the result stays
  schema-valid.
- Hypothesis profiles: `dev` (default, explores new examples) and `ci` (derandomised,
  `HYPOTHESIS_PROFILE=ci` in CI, so failures reproduce). fast-check covers the TypeScript side.
- The status engine (F04): never VERIFIED while an unexplained stable gap or a failed
  invariant exists, over 2000 generated inputs per run (`test_status.py`), beside a table of
  every rule.
- Golden cards (F05): each fixture's card through `repro run`, in
  `engine/tests/golden/cards/<fixture>.json`, with what differs between two runs made fixed
  (`card_support.normalised`: IDs, times, the machine, Repro's version, the log's digest and
  size, timing numbers). Every run also checks that its card, assembled again from the stored
  log, is byte-identical to the one it published.

## 4. Cross-platform

CI runs on `ubuntu-latest` and `windows-latest`. Tests cover:
- paths with backslashes, spaces, and long paths (`core.longpaths`),
- CRLF in target repos (hashing and diffing must be line-ending independent where intended),
- worktree creation and cleanup when files are locked (Windows),
- subprocess termination on timeout (Job Objects on Windows, process groups on POSIX).

## 4a. Failure modes (H01)

`docs/FAILURES.md` catalogues what can fail in a run. `test_failures.py` (marker `chaos`) meets
each failure for real through `repro run`:
- fixture repositories whose head breaks something (`fixtures/failures.py`);
- an API nobody answers;
- Docker or git missing.

Each test checks the missing item's reason, that its detail is one line with no traceback and
no home directory, and the card's status. CI runs them in the `chaos` job on Ubuntu and
Windows, apart from the test matrix.

## 5. Agent evaluation (summary)

Detailed in `docs/ARCHITECTURE.md` §Agents and `docs/paper/README.md`.
- **CI (deterministic):** agent calls are served from cassettes keyed by prompt version, model
  and input digest (run-specific IDs normalised; G02). A missing recording fails the test. It
  never silently calls the API: every test runs against a closed loopback API with a dummy key,
  and tests that start the Claude Code CLI fail on any connection past loopback.
- **Eval suite (live):** labelled PRs with expected claims and verdicts. Tracked metrics:
  claim-extraction precision/recall, verdict accuracy, **citation validity rate** (cited
  evidence exists and relates to the claim's targets), rejection rate, tokens and cost per PR.
  Results are appended to `bench/results/agent-evals.csv` with model ID and prompt version.
- **As built (G09).**
  - `repro_bench.verdicts` runs the five reviewed pull requests end to end, from their
    cassettes, twice each: as configured, and with every verdict judged in both orders.
  - It scores the runs against the claim set's verdict labels and the fixtures' expected cards:
    verdict accuracy, unsafe verdicts, status accuracy, citation validity and rejection rate
    per reviewer answer, flip rate, and dollars and tokens per pull request.
  - `agent-evals.csv` holds one row per metric. The `agent evals` CI job
    (`pytest -m agent_eval`) holds the metrics to `bench/data/thresholds.toml`.

## 5a. Web quality gates (B12)

Each gate has a self-test in CI that injects a regression of its kind and requires the gate to
fail (`REPRO_INJECT`):

| Gate | Runs | Measure | Injected regression |
|---|---|---|---|
| Visual (`visual` job) | Every PR | Playwright, Chromium + SwiftShader, pinned image; screenshots at 12, 24.5, 30.5 s, the end, the drawer and reduced motion; at most 50 differing pixels | Every panel shifted by 1 px |
| Size (`test`, Ubuntu) | Every PR | size-limit: all shipped JS, gzip, 300 kB | 300 kB of random data in the entry chunk |
| Allocation (`test`) | Every PR | Heap plus ArrayBuffers after GC, 1,000 frames after warm-up, under 512 kB of growth | A 2 kB-per-frame leak |
| Perf (`gates`, nightly) | Every PR (self-test), nightly | Frame cost p50/p95 divided by a calibration workload, at most 30% above the CI baseline | 2 ms of work per frame |

Baselines (screenshots, perf) are produced by CI and committed from its artifacts, never
generated on a developer machine.

## 6. CI tiers

| Trigger | Jobs |
|---|---|
| PR | static, schema, unit, property, fast golden logs, small integration set, secret scan, bundle size, visual regression (if web changed) |
| Push to `main` | PR set + full integration |
| Nightly | full integration, end-to-end, performance, security suite |
| Stage gate | everything + mutation testing + live agent eval + benchmark smoke run |
