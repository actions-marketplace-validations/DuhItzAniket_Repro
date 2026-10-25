# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Phase IDs refer to [`docs/ROADMAP.md`](docs/ROADMAP.md).

## [Unreleased]

### Added
- `sandbox = "e2b"`: each side of a run in a Firecracker microVM of a self-hosted E2B
  (Embed), at L5. Each side is sealed before any of the target's code runs:
  - its network is closed by the VM's own kernel firewall, and checked from inside;
  - the code runs as a user without sudo.

  E2B's own egress firewall was measured letting TCP to numbered addresses through, so it
  is only a second layer. Settings `e2b_memory_mb` and `e2b_cpus`, and E2B's SDK as the
  `e2b` extra. CI job `e2b`. [K09]

### Changed
- Event schema: `e2b` is a sandbox backend. [K09]
- The runners treat every sandbox whose snapshot is elsewhere alike (`RemoteSandbox`):
  Docker's and E2B's. [K09]
- Both packages' PyPI pages: links to the repository, documentation, changelog, issues and
  DOI, classifiers and keywords, and a description written for PyPI, with absolute links
  (`engine/PYPI.md`, and repro-events' README).

### Fixed
- A run whose process is killed (its terminal closed, the machine stopped) no longer stays
  "running" forever in the store and the fleet view. The process holding a run renews a lease
  on it every minute. The next Repro process that opens the store ends a run whose lease is ten
  minutes old as `failed`, with no card made up for it and its event log left as it was. The
  store's schema is at version 2 (a column, `heartbeat_at`). Found when the GitHub App's run
  was cut off on 2026-10-21. [C02]

## [1.4.0] - 2026-10-22

Isolation level L5, each container in a Kata microVM (K10), and Repro as a GitHub App on the
operator's own machine (I04). Release record: `docs/phases/release-1.4.0.md`.

### Added
- Isolation level L5: with `docker_runtime = "kata"`, each of the target's containers runs in
  a Kata Containers microVM with Cloud Hypervisor, on a kernel of its own. The card states L5
  only when a container of the runtime reports a kernel other than the engine's, whatever the
  runtime is called. `scripts/kata/setup.sh` sets up a Linux host with KVM (WSL2 on Windows 11
  included): Docker Engine and Kata 4.2.0, pinned and checked, then a self-test.
  Kata's VM does not apply Docker's pids limit (`pids.max` reads `max` in it; a fork bomb
  started 30 processes against a limit of 16), so at L5 the sandbox also sets uid 1000's
  `nproc`, which a kernel of the container's own counts for the container alone. [K10]
- CI job `kata`: the container sandbox and a run in Kata microVMs on GitHub's runners, which
  allow nested virtualization without supporting it; not required. [K10]

- `repro app register` and `repro app serve`: Repro as a GitHub App on your own machine. It
  runs each pull request of the repositories it is installed on and reports the card as a check
  run, with a **Re-run Repro** button, and one comment. Registration uses GitHub's manifest
  flow, and the App's key and secret go straight into Repro's data directory. Deliveries come
  through a smee.io relay or a port of the App's own, and each is checked by its signature and
  delivery ID. A fork's pull request runs only when someone with write access clicks **Run
  Repro**. Each run has a token for one repository and one hour, and runs in Docker. Settings
  `app_repositories` and `app_concurrency`. Guide: `docs/guide/github-app.md`. [I04]

### Changed
- Event schema: `L5` is an isolation level, and `kata` a sandbox backend, in `run.started`.
  [K10]
- `repro report`'s reporting takes a reporter's identity, App ID, buttons and external ID, for
  the GitHub App; the Action's reports are unchanged. PyJWT, already installed, is a direct
  dependency. [I04]

## [1.3.1] - 2026-10-21

A patch release with no change to Repro's code: CI's timeout, the Marketplace listing in the
docs, and the first release archived on Zenodo, for a DOI. Release record:
`docs/phases/release-1.3.1.md`.

### Changed
- CI: the Python tests' job may take 45 minutes, up from 30. On Windows it took 22 to 27
  minutes with K08 and K11's tests, and once passed 30, which cancelled it on `main`. [K11]

## [1.3.0] - 2026-10-20

JavaScript and TypeScript, end to end: the rest of ADR-0017 (K06, K07, K08, K11) and the
fixes the work around it found. Release record: `docs/phases/release-1.3.0.md`.

### Added
- `repro js-run` reports which tests a change reaches. For each test, the functions it ran,
  from V8's precise coverage, are recorded by a setup file added after the project's own:
  a wrapper config for Vitest, and `setupFilesAfterEnv` from `--showConfig` for Jest. A
  changed function selects the tests that ran it; a change outside any function selects
  the tests whose files loaded it (`changes`, `selected`). [K06]
- `repro js-run` compares how a change's functions behave, base against head, and reports
  the differences no test catches (`behaviour`), with values.
  - **Wrapping:** for Vitest, a Vite plugin wraps the changed functions in place; for Jest,
    they are wrapped through their modules' exports.
  - **Recording:** each call's arguments and result are encoded by `repro_encode`'s rules,
    ported to JavaScript; no user code runs.
  - **Comparison:** base runs twice, the second time shuffled, then head, compared as
    Python's traces are. [K07]
- `repro run` reproduces JavaScript and TypeScript changes tested with Vitest or Jest, and makes
  their card. A JavaScript run emits the same events as a Python one, so the cockpit, the
  exports, the GitHub report and the review stage all read it. [K08]
  - **Language:** `run.started` records it. `repro run` reads it from the repository's root
    (a Python project's files, else a `package.json`), or takes `--language`.
  - **Analysis:** changed functions and the imports between modules, by scanners.
  - **Runs:** base, head, then base again shuffled; a test that flips runs three more times on
    each side. Outcomes are classified as Python's are.
  - **Checked:** each test's outcome; the changed functions' behaviour; the tests a change edits
    (removed, newly skipped, fewer `expect` or `assert` calls); the dependencies, declared and
    installed; the invariants.
  - **Not checked yet**, and listed on the card: the public API, weakened assertions, timing,
    and the network. A Node process is neither blocked nor seen in the process sandbox (L2).
    These are K11.
  - **Installs:** from the lockfile with lifecycle scripts off, one side after the other. An
    install that leaves no runner in `node_modules` is an environment failure that says so.
- `repro doctor` reports Node.js. [K08]
- JavaScript and TypeScript runs in a container with Node and no network (L3; L4 under gVisor),
  under `auto` when Docker answers, as Python's are. Repro's Node.js image is Node 22, with pnpm
  and Yarn through Corepack, all pinned by digest or SHA-512. The install has a container of its
  own, the only one with the network; the tests run in hardened containers with none. [K11]
- A JavaScript or TypeScript change's public API, read by Repro's own pinned TypeScript compiler
  (6.0.3, checked against the registry's sha512, never the target's): each export of the
  package's entries, base against head, as `diff.api` breaks. The new breakage kind
  `parameter_changed_type` covers a parameter whose type changed. [K11]
- Edited JavaScript tests are read from the compiler's syntax tree: a weakened assertion
  (`toBe` to `toBeTruthy`, say), a widened `toThrow` and fewer assertions. Base's version of
  an edited test also runs against head's code, so a test edited to fit a regression is
  `base_test_fails_on_head`. [K11]

### Changed
- The Action's name is `Repro Reproduction Card`, so it can be listed on GitHub Marketplace,
  whose names may not match a GitHub user's (one is named `repro`). Workflows refer to it as
  before, `uses: DuhItzAniket/Repro@<ref>`, and its check run is still named `Repro`. [I03]
- The event schema: `run.started.language`, optional, so older logs still validate and replay
  to the same card. `env.provisioned` takes npm, pnpm and Yarn projects and a Node.js version;
  the card's conditions add the language, Node.js and the shuffled run's seed when the log
  records a language. [K08]
- A JavaScript function's symbol in `repro js-run`'s `behaviour` is a `SymbolId`
  (`src/m.js:add#0`), as on the card. [K08]

### Security
- A JavaScript install could run the project's own code, even with lifecycle scripts off.
  pnpm runs a project's `.pnpmfile.cjs`, and Yarn 1 runs the file `.yarnrc`'s `yarn-path` names
  in its place; each wrote a marker file during an install with the network on. Installs now
  pass pnpm's `--ignore-pnpmfile` and set `YARN_IGNORE_PATH=1`, on the host and in containers.
  K08's host installs had the same exposure. [K11]

### Fixed
- `run.started` named the process sandbox (`mode.sandbox_backend: "process"`) for every
  `repro run`, even when its tests ran in Docker. It now names the sandbox the run uses:
  `docker`, `gvisor` under `docker_runtime = "runsc"`, or `process`. With `auto`, Docker is
  asked once per run, and the sandbox jobs use that answer; they used to ask once per side.
  An `--analyse-only` run starts no sandbox, so Docker is not asked and `auto` is recorded as
  `process`. A JavaScript run takes the process sandbox under `auto` until K11's container, and
  says so. The card was not affected, because it takes its isolation level from `sandbox.ready`. [C08]
- An event log could have an event's timestamp 1 ms behind the event stored before it, and
  `repro-events validate` warned about it. The cause was two jobs emitting at once, such as
  snapshots, analysis and policy in the build stage: each event is stamped on its job's
  thread, and the store's writer numbers the events later. The writer now stores an event
  stamped before its run's latest event with that latest timestamp. `seq` is still the only
  order. The end-to-end tests that expect a clean log no longer fail now and then. [C03]
- The `net-call` fixture's end-to-end test no longer flakes on Windows. Head's lookup of a
  `str` host loaded Python's `idna` codec, about 4 ms in every timing run, against the 5 ms
  timing floor, so the run reported a regression in 1 of 20 runs here. The fixture's host is
  now `bytes`: the same refused lookup and connection, and `no_network` still fails. [F03]

## [1.2.0] - 2026-10-20

Stage K's first items, the evaluation's harness (Stage J, J01 to J05; the study itself
deferred) and the fixes a real-world demo called for. Release record:
`docs/phases/release-1.2.0.md`. The first release published to PyPI.

### Added
- `python -m repro_bench.swe`: SWE-format instances and agent patches as Repro runs, under the
  evaluation's input contract. `seal` keeps a system's input to the repository at base, the
  diff and the claim, from an allow-list (the ground truth and the instance's ID are withheld),
  and writes a manifest pinning each instance's source, base commit, image digest and patch
  SHA-256; `run` makes head without a working tree, runs Repro and logs the input's SHA-256
  with each card. [J01]
- Ground truth for the evaluation (`bench`): tier A runs a task's hidden tests and full suite
  in its own image (no network, nothing mounted) and grades them as SWE-bench does; tier B runs
  UTBoost's augmented tests and model-written differential tests against the gold patch and
  the candidate, 20 times each, counting only stable tests; `python -m repro_bench.groundtruth`
  labels what it can and writes the rest, with 15% audits, as the annotators' batch. [J02]
- A blinded annotation page per annotator (`python -m repro_bench.annotate`), Cohen's κ per
  source, adjudication and a frozen label file (`python -m repro_bench.labels`), and the
  annotation guideline (`docs/paper/ANNOTATION_GUIDELINE.md`). [J02]
- The evaluation's baselines (`python -m repro_bench.baselines`), on J01's sealed inputs and by
  rules fixed before any study data (`docs/paper/BASELINES.md`): B0 runs the project's suite on
  base and head in the task's image; B1 reviews the claim and diff with Repro's reviewer model
  and budget; B2 adds read-only tools over the repository; B3 is PR-Agent v0.48.0, pinned,
  with the same model. Testora is not run, for stated reasons. [J03]
- Setting `sandbox_image` (`REPRO_SANDBOX_IMAGE`): run the tests in an image's own Python
  environment, as it is, pinned by digest. Examples are a benchmark task's image and a CI
  image. The image's named conda environment is used when it has one. The snapshot's code
  must import from the snapshot, or the card is UNVERIFIABLE and names the reason. [J04]
- `repro_bench.seed` (plausible-but-wrong patches for S0, kept only when tier A finds them
  plausible), `repro_bench.memo` (the memorisation probe) and `repro_bench.power`
  (McNemar sample sizes, approximate and exact). [J04]
- S2's candidate tasks, pinned (`bench/data/s2-candidates.jsonl`, 351 tasks by the protocol's
  rules; `python -m repro_bench.tasks`), and `python -m repro_bench.generate`, a two-call
  localize-and-repair scaffold whose edits become a diff by `difflib`, its trajectory kept. [J05]
- The study's registered analysis as code (`python -m repro_bench.analysis`): majority of k,
  flip rate, coverage and bounding conversions, exact McNemar with Holm, and cluster-bootstrap
  intervals over repositories. Also the pre-registration and its freeze checklist
  (`docs/paper/PREREGISTRATION.md`). [J06]
- `repro js-run` (experimental): a JavaScript or TypeScript change tested with Vitest or Jest.
  Each side's dependencies are installed from its lockfile with lifecycle scripts off, its
  tests run in the process sandbox, and the outcomes are compared test by test. There is no
  card yet: the tracer and the card are proposed in ADR-0017. [K01]
- The fleet view (`/fleet.html`, and `GET /fleet` in the API): every run of the engine's data
  directory as a tile, live. A tile shows running, then the card's status, and flashes only
  when its run writes events. A tile opens its run in the cockpit. [K02]
- `repro mcp`: Repro as an MCP server on stdio, for coding agents. The agent names a repository
  and two commits; Repro runs them in its sandbox, and the agent reads the status, the card
  and the evidence. Every other tool only reads. See `docs/guide/mcp.md`. [K03]
- `repro run --no-serve` prints `repro: <run id> started` when the run begins. [K03]
- `repro symbolic` (experimental): CrossHair's `diffbehavior` on the small, pure, annotated
  functions a change edits. It runs base's body against head's in head's environment, in the
  sandbox, and reports an input on which they differ. Functions it does not check are named
  with why. [K04]
- Setting `docker_context` (`REPRO_DOCKER_CONTEXT`): every container command goes to the Docker
  context named, such as a team's engine on another machine over SSH. ADR-0018 (proposed)
  designs microVM sandboxes (E2B self-hosted, Kata with Firecracker). [K05]
- `scripts/kick_the_tires.py`: the evaluation's deterministic pipeline on one pinned fresh task
  in about half an hour, without keys. Also the artifact guide (`docs/paper/ARTIFACT.md`) and
  the archive metadata (`.zenodo.json`, `CITATION.cff`). [J09]
- The paper's draft 0 (`docs/paper/draft.md`), its results marked pending, and the owner's
  submission checklist (`docs/paper/SUBMISSION.md`). [J10]

### Fixed
- A call whose arguments cannot be encoded (a framework's context object, too deep, too
  large or cyclic) is compared by its position, the n-th such call of the function in the
  test, when base twice and head made as many of them; its result was never compared
  before. On pallets/click#3884, whose changed functions take click's `Context`, 151 calls
  of 2 of its 4 changed functions (in 16 and 58 tests) went from not comparable to compared
  (395 calls in all, up from 244), and the card from UNVERIFIABLE to VERIFIED
  (deterministic). [E03, E04]
- A run stopped by a warning the project makes an error (`filterwarnings = error`) is reported
  by that warning, not by pytest's "ImportError while loading conftest". [J01]
- A selection of tests too long for a command line (889 node IDs on Windows,
  `WinError 206`) no longer fails every test step: past 8,000 characters the IDs go in a file
  Repro's pytest plugin reads, in the order given. [J04]
- The evaluation's task images no longer need all of S2's disk at once. Sealing pins an image
  that is not on the machine by its registry, without pulling it. Ground truth, B0 and
  `swe run` take `--manifest` and run each task in the image pinned for it (`swe run` used one
  image for every task, and B0 the dataset's tag). With `--image-budget-gb`, images are pulled
  as their tasks come and the least recently used ones the harness pulled are removed; an image
  that was on the machine before is never removed (`repro_bench.images`). [J01, J04]
- A reviewer can judge a change to a real repository within a free tier's 8,000 tokens a
  minute. With more than 10 items of evidence it is shown what differs first, alike
  findings grouped once, and what says nothing changed only counted, and past 40 evidence
  IDs its answer's schema no longer lists every ID. A tool's answer stops at 4,000 characters,
  at a whole line, and says how to ask for the rest. On an itsdangerous change (304 items, 272
  of them tests that passed on both sides) each request had been 10,786 to 19,909 tokens and
  the reviewers saw passing tests where the 27 behaviour differences were. [G03, G05]
- An agent asked for a structured answer through an OpenAI-compatible API is asked for it in
  its last two turns, however many lookups it still wants, instead of ending with none; a tool
  call where the answer was due (Groq: "Tool choice is none, but model called a tool") is
  asked again like an answer off the schema. The review job may take 30 minutes for all its
  claims, up from 10: a rate-limited API serves them one after another. An agent waiting on a
  rate limit or an unanswered request says so in its state ("rate limited (429); asking again
  in 85 s") instead of looking busy, and a wait longer than the call has left ends the call at
  once with the reason (a spent daily limit asked for 1,287 s). [G05, G10]

## [1.1.0] - 2026-10-18

Stage I — GitHub integration. Gate record: `docs/phases/I-gate.md`.

### Changed
- An attestation's subject may be the card itself (its canonical JSON, by `cardDigest`), as
  GitHub signs it; `card_from_attestation` checks either subject against the card. [I03]

### Added
- `repro run --pr <url>`: a GitHub pull request, from its address alone. Its base, head,
  title and description come from GitHub's REST API, its commits, a fork's included, are
  fetched into a mirror of Repro's own, and its head is compared with where it left its base
  branch (their merge base). A token from `GITHUB_TOKEN` or `GH_TOKEN`, when set, goes to
  GitHub only, and on no command line. `--repo` uses a clone of yours as it is. [I01]
- Setting `github_api_url` (`REPRO_GITHUB_API_URL`), for GitHub Enterprise Server. [I01]
- `repro report <run>`: the card on its GitHub pull request. The check run `Repro` on the head
  commit (VERIFIED `success`, FLAGGED `failure`, UNVERIFIABLE `neutral`), with annotations
  at the changed code, 50 a request; one comment, found by a hidden marker and written by the
  same identity; the job's summary in GitHub Actions. A rerun updates them; a target it
  cannot update is named, and the others are still made. [I02]
- The GitHub Action (`action.yml`, `uses: DuhItzAniket/Repro@<ref>`): Repro on every pull
  request, run on the runner with its Docker sandbox, the card reported and kept as an
  artifact, and, on a public repository, signed with `actions/attest` (the card's canonical
  JSON as subject, Repro's predicate). A fork's pull request is not posted to unless the
  workflow opts in under `pull_request_target`. Repro's mirror and test maps are cached.
  [I03]

## [1.0.0] - 2026-10-17

Stage H — Hardening & first release. Gate record: `docs/phases/H-gate.md`.

### Added
- The failure catalogue (`docs/FAILURES.md`). Each way a run can fail is listed with what the
  run records, its effect on the card, what resolves it, and its test. Chaos tests meet each
  one end to end through `repro run`, in a CI job of their own on Ubuntu and Windows. [H01]
- Settings `test_timeout_s` (900) and `memory_limit_mb` (4096) for each sandboxed run's
  limits. [H01]
- A missing reason, `memory_limit`: a run that reached its memory limit says so (Docker
  stopped it, its peak reached the limit, or tests failed with `MemoryError`). [H01]
- `repro_bench.scale`: `repro run` on three pinned Pallets changes of increasing size
  (itsdangerous, flask, click), cloned at run time, cold and then warm. Times, cache use and
  the card's counts go to `bench/results/scale.csv`. [H02]
- A session token for the engine's API. Each server `repro serve`, `run` or `replay` starts
  makes its own, and every request for a run's data must carry it; the cockpit's own files
  need none. The printed URL holds it in its fragment, which a browser never sends. The cockpit
  keeps it for the tab and sends it with each request. The browser is opened on a page only the
  user can read, not on a command line holding the token, and the server keeps no access log.
  [H03]
- `docker_runtime` (`REPRO_DOCKER_RUNTIME`): `runsc` runs the target's containers under gVisor,
  isolation level L4. A runtime Docker does not have is an error, never a silent L3. CI runs
  the container tests under gVisor in a job of their own. [H03]
- The container escape suite also tries becoming root, a raw socket, mounting a filesystem,
  Docker's own socket, writing a kernel setting, opening kernel devices and reading the host's
  environment. [H03]
- A dependency audit in CI: pip-audit 2.10.1 over the locked Python dependencies (hashes
  checked) and `pnpm audit`. [H03]
- THREAT_MODEL.md §7 names, for each threat, the tests that meet its mitigation or why none
  can; a test keeps the two in step. [H03]
- `repro export --format in-toto|sarif|junit`. An unsigned in-toto Statement v1 of the card,
  with Repro's predicate: described at `docs/attestation/reproduction-card/v0.md`, schema in
  `packages/events/schema/attestation`. It holds the card with its digest, the evidence the
  card cites as digested resource descriptors, and the event log's digest. Taking the card back
  out checks every digest; the round trip is exact. Also SARIF 2.1.0, with a result per gap
  item, failed invariant, judged claim and missing item, and JUnit XML, with a test case per
  claim and one for the status. Each is checked against its schema: Repro's own, OASIS's SARIF
  2.1.0 schema, and Apache Ant's JUnit XSD. [H04]
- Release wheels: `repro-engine`, with the cockpit inside, and `repro-events`, with the event
  schemas (`scripts/build_wheels.py`). A published GitHub release gets them attached by the
  release workflow. The README gives the install, one `uv tool install` from the two wheels.
  CI follows it on Linux and Windows for every change and makes a first run on a small
  repository, inside ten minutes (`scripts/install_check.py`). [H05]
- `repro doctor` reports the cockpit: the checkout's build, the wheel's copy, or none. [H05]
- The user guide (`docs/guide/`): a quickstart, reading a card, the `repro.yml` reference,
  troubleshooting, and a page for contributors that follows a run through the code. [H06]
- A recorded demo (`docs/guide/demo.md`): one real run of a change no test catches, with stills
  of the cockpit taken from its replay and its card. `apps/web/gate/demo.spec.ts` makes the
  recording again. [H06]
- A missing reason, `run_incomplete`: a test run that ended early or ran past its limit. Its
  detail ends with the line of the run's output that names its error. Such runs were
  `environment_failed` or `timeout` items. [H06]
- `repro run`'s last line gives the card's status. [H06]

### Changed
- A selected test that no run reported leaves the change UNVERIFIABLE (`tests_not_run`). Until
  now only a run with no outcome at all did, so a run that timed out part way could pass the
  rest. [H01]
- Missing items read as something to act on:
  - a failed job names its error, by type and first line, with the home directory written
    `~`;
  - a collection error gives the line that names the error;
  - a timed-out run says how long it ran;
  - a sandbox that cannot start says to start Docker or use the process sandbox.

  The Markdown card shows what would resolve each item. [H01]
- The OpenAI-compatible backend retries an API it cannot reach and an answer it cannot use,
  as it retries rate limits and server errors; an unusable answer no longer crashes the job.
  Any failure of the probe writer leaves Hypothesis's probes running. [H01]
- Runs are faster on real repositories. On the three references, cold, on one Windows
  machine: itsdangerous 38 s → 18 s, flask 402 s → 110 s, click 1,151 s (base's run stopped
  at its 900 s limit) → 34 s. A warm run takes 15 s, 90 s and 26 s. [H02]
  - Provisioning installs the dependency groups the tests need (`test`, `tests`, `testing`,
    else `dev`), not every group: flask's environment goes from 80 packages (237 s) to 16
    (6 s).
  - A project's pytest configuration (`addopts` in `pytest.ini`, `pyproject.toml`, `tox.ini`
    or `setup.cfg`) keeps its selection options (`-m`, `-k`, `--ignore`, `--deselect`,
    `-p no:…`, …) when Repro runs its tests. click deselects about 31,000 stress cases this
    way; Repro used to collect and run them.
  - Base and head run at the same time where they are independent: base's mapped run with
    head's listing, base's second run with head's, and each rerun of a flip.
  - The timing pass leaves out tests already under the timing floor in every traced run, and,
    after its first ABBA block, those under it in every sample. They are reported as too fast
    to time, as before.
- A requirements directory (`requirements/tests.txt` and the like) adds the project's own
  dependencies from `pyproject.toml`. [H02]
- The run's log says where base's test map came from: made by running every base test, or
  from the cache. [H02]
- The home directory is written `~` in every string of every event, as on the card's missing
  items: a card may be shared, and an absolute path names its user. Log messages, values and
  errors pass the same credential scrubbing as events. [H03]
- CONTRIBUTING lists every CI job and what it checks. The engine's README says the API needs
  the session token. [H06]
- The install check requires its first run's card to be VERIFIED, with a selected test and
  nothing missing but the timing of a test too fast to time: completing is not enough. [H06]
- The cockpit server's own log lines are JSON like Repro's, through the same handler and the
  same scrubbing. [H06]
- The Markdown card shows five targets of a missing item and counts the rest; the JSON card
  keeps them all. [H06]

### Fixed
- `repro run` without git installed prints a message and exits 2, not a traceback. [H01]
- The cockpit's drawer says "None." of an empty table once the run has finished, and on the
  published card; "None yet." only while the run can still add to it. [H06]
- An installed Repro ran no test in a container on Docker Desktop for Windows: the container's
  user could not read Repro's pytest plugin, bind-mounted from the installed package. The hook
  is now copied into a volume of its own and mounted read-only, so nothing of the host is
  mounted. [H06]
- A change could be VERIFIED when every test run ended early and nothing was selected (base's
  map and head's listing died, and no function changed). A test run that does not finish now
  leaves the change UNVERIFIABLE (`tests_not_run`), whatever was selected. [H06]
- A VERIFIED card said "everything was checked" while listing what it could not check; it says
  so only when nothing is missing. [H06]
- The session token reached the terminal's log: uvicorn logs each WebSocket it accepts with its
  query, `?token=…` included, and the access-log switch does not cover that line. A credential
  in a query string is now redacted from every log line. [H06]
- A change to a class's own code (an attribute, a base class, a decorator) selected none of the
  tests that use the class: that code runs at import, never inside a test. It now selects the
  tests that ran one of the class's methods, as coverage already counted them. [H06]
- A run from a cached base map reports what the run that made the map did. Base's first run
  reaches the cockpit, and base's collection errors are kept with the map; a warm run used to
  drop both. Of base's first run, only what the selected tests attempted (network, processes,
  file writes) counts; a cold run used to report the attempts of every base test. [H02]
- A file name with a byte that is not UTF-8 (held by Python as a lone surrogate) no longer
  fails the job that reports it: text shows it escaped, a target path as U+FFFD. Found on
  click. [H02]
- In containers, a project built with flit_core (itsdangerous, flask, click) can be
  installed. The snapshot's files reached the container dated 1970, and a wheel, being a
  zip, cannot hold a time before 1980. They now carry the commit's time, as `git archive`
  does, never earlier than 1980-01-02. [H02]
- The audit hook judges a write by where its path resolves. A link inside the run's directory
  that leads out is now reported. A path that reaches the run's directory through a link, a
  junction, a short name or a redirected folder no longer is. [H03]
- A cockpit opened without the session token says to open the printed URL, instead of showing
  the demo run. [H03]

## [0.7.0] - 2026-10-17

Stage G — Agents. Gate record: `docs/phases/G-gate.md`.

### Added
- The LLM backend: every model call goes through an `LLMBackend` protocol, whose result says
  how the call ended (`ok`, `max_turns`, `max_budget`, `timeout`, `invalid_output`, `error`)
  instead of raising. The default runs the Claude Agent SDK under the lockdown of ADR-0007:
  no built-in tools, only the call's own read-only `mcp__repro__*` tools (and
  `StructuredOutput` for a structured answer), a hook that denies every other tool, no
  settings, CLAUDE.md, memory, plugins, skills or other MCP servers, an empty working
  directory and configuration directory per call, and a turn, dollar and time budget on every
  call. Tests run the bundled CLI against a local stand-in for the Messages API: files planted
  in the target repository or a configuration directory, shown to load on an unlocked CLI,
  never load under the lockdown. New dependency: `claude-agent-sdk` 0.2.167. [G01]
- Agent dispatch: at most `min(12, free RAM in GiB)` agents at once, in the cockpit's 12
  dispatch slots, reported as `agent.spawned`, `agent.state` (`waiting` when queued, then
  `thinking` and `tool_call` with the share of the turn budget used, then `done` or `failed`)
  and `cost.estimate`; cancelling a run cancels its agents' calls. [G01]
- Cassettes: agent calls recorded once and replayed offline, keyed by prompt version, model
  and a digest of the input taken with run-specific IDs normalised, so a recording replays in
  any later run with that run's evidence IDs. A missing cassette fails instead of calling the
  API; a replay whose recorded tool calls now return something else fails as stale. Cassettes
  are canonical, scrubbed JSON. [G02]
- Versioned prompt files (`version:` front matter, `string.Template` placeholders filled once)
  with a lock of every version's SHA-256, so a version always names one text. [G02]
- Cost accounting: usage per model from the CLI, one `cost.estimate` per model with a new
  optional `source` (`live` or `cassette`), and a run ledger that, given a limit, starts no
  agent call once the run's budget is spent. [G02]
- Tests never reach a model or the internet through the CLI: every test runs against a closed
  loopback API with a dummy key, and tests that start the CLI fail on any connection past
  loopback. [G02]
- Evidence tools for agents: `list_evidence`, `get_evidence`, `read_snippet` and `get_diff`,
  reading only the run's evidence and the commits' git objects (never the snapshots the
  target's tests ran in), by checked paths, with size caps. Evidence is rendered the same in
  every run and on every machine, and everything from the target is delimited as data it
  cannot step out of. Fixture `injection`: honest-fix with instructions to the reviewers
  planted in its text; its tool outputs and card equal honest-fix's. [G03]
- A second LLM backend for OpenAI-compatible APIs (Groq, local servers, routers), decided in
  ADR-0016: Repro runs the tool loop with only the call's own tools, asks for structured
  answers alone and checks them against the schema, honours rate limits, and prices calls from
  sourced price tables. The Claude Agent SDK stays the default; Stage G's recordings and
  evaluation use Groq's gpt-oss-120b. [G10]
- The reader agent: with agents on (`agents` setting: `claude`, `groq` or an
  `openai`-compatible endpoint; off by default), a run reads the pull request's claims from its
  title and description (`--pr-title`, `--pr-body`, `--pr-file`) or its commit messages, as
  `claim.extracted` events: atomic, typed, tied to the files the change touches. A reader that
  ends without an answer is an `agent_failed` missing item. Agent settings: `agent_model`,
  `agent_mode` (live, record, replay), `agent_cassettes`, `agent_budget_usd` (2.0 a run). [G04]
- The labelled claim set (`bench/data/claims.toml`: 18 pull requests, 43 claims) and its
  evaluation (`python -m repro_bench.claims`). The reader on gpt-oss-120b: precision 0.955,
  recall 0.977, replayed in CI from its cassettes against thresholds of 0.90 (approved at
  Gate G); the results in `bench/results/agent-evals.csv`. [G04]
- Reviewer agents: after every other job, each claim is judged against the evidence about its
  files, shown in full with their diffs and the PR's other claims. A verdict must cite that
  evidence (an enum of its IDs, at least one) and quote it exactly, and is checked before it
  counts: unknown or unrelated citations, misquotes, support resting on unstable evidence, or
  "no change" supported by a change the other claims do not describe are rejected, retried
  once, then recorded as `verdict.rejected` and a missing item. On Groq's gpt-oss-120b, from
  cassettes replayed in CI: honest-fix and its injection twin VERIFIED with the same verdicts,
  silent-change and false-claim FLAGGED. [G05]
- The gap reviewer: after the review, each difference no verdict accounts for is mapped to the
  supported claims that describe it, or left unexplained. The mapping is checked like a
  verdict (given differences and claims only, about the difference's files, exact quotes) and
  becomes supporting verdicts that cite what they explain; unexplained differences flag. No
  call is made when nothing needs explaining or no claim could explain it. [G06]
- Evidence relevance follows test selection: a test is evidence about the code it was selected
  for. Groq's "Failed to validate JSON" is an invalid answer, asked again. Recording keeps
  existing cassettes and calls the model only for new or changed calls. [G06]
- The probe writer: with agents on, an agent shown each changed function E06 can probe (its
  definition, its parameters' types and the change to its file, never the PR's claims)
  proposes realistic inputs for it as data. Inputs that do not name exactly the function's
  parameters with values of their types are dropped and counted; the rest run beside
  Hypothesis's on base, base again and head, and a difference says which inputs found it
  (`found_by`). If the agent fails, the card lists it and Hypothesis's inputs run anyway.
  The new `agent_probes` setting switches the probe writer off. New fixture
  `probe-semantic`: a change wrong only for half-hour UTC offsets, which random inputs never
  reach. [G07]
- An agent whose cassette is missing or stale is shown as failed. [G07]
- Bias controls for the verdicts:
  - a `contradicted` verdict is judged again with the evidence and the other claims in the
    opposite order, and is kept only if that order gives it too (otherwise `unverifiable`,
    with `order_swap_disagreement`);
  - every verdict records how many judgements it rests on and how many agreed;
  - an optional panel of three models, two of which must agree (`agent_panel`);
  - the card says when the reviewers share the model family the change's commits name as
    an author (`judge_is_author`).

  `agent_order_swap` asks for the second order on every verdict, or on none. [G08]
- The QA agent: after the review, every claim's verdicts are read together. A supported
  verdict that cannot hold with another is withheld as `unverifiable`, which can make a card
  UNVERIFIABLE, never FLAGGED. [G08]
- The agent evaluation harness: the reviewed pull requests run end to end from their
  cassettes (or recorded live), scored against hand-set verdict labels and the fixtures'
  expected cards. It measures:
  - verdict accuracy and unsafe verdicts;
  - status accuracy;
  - citation validity and rejection rate per reviewer answer;
  - flip rate under order swap;
  - cost per pull request.

  The results are appended to `bench/results/agent-evals.csv`, now one row per metric. A CI
  job, `agent evals`, fails on a regression beyond the thresholds. Every refused reviewer
  answer is now a `verdict.rejected`. [G09]

### Fixed
- Cassette keys normalise run-specific IDs at the start of a line, which the old key left
  as they were (they follow an escape in the call's JSON): such a call recorded once now
  replays in any run, and every other key is unchanged. [G08]

## [0.6.0] - 2026-10-15

Stage F — Policy & deterministic card. Gate record: `docs/phases/F-gate.md`.

### Added
- Repository policy: `repro.yml`, read from the base ref and validated against a JSON
  Schema (the invariants, claim and coverage thresholds of PRODUCT.md), with errors that
  name their line, column and path. An invalid file means no invariant is evaluated, and
  says why; head's edit to the file is `policy_change` evidence naming how it would weaken
  the policy. YAML is read with the safe loader, bounded, without aliases. Fixture
  `policy-weaken`. New dependency: PyYAML. [F01]
- Invariants: `public_api_stable`, `no_new_dependencies` and `forbidden_paths`, when base's
  `repro.yml` enables them, are decided from the run's own API and dependency evidence and
  the change's files: `invariant` evidence citing what it rests on, and an
  `invariant.result` check. Anything they need that was not observed makes them "not
  evaluated", never a pass. Fixtures `forbidden-path` and `invariants-pass`. [F02]
- Jobs can come `after` others without needing them to succeed: the invariants, the API
  comparison and the dependency comparison now run when a side could not be installed or
  tested, so a dependency that does not install is still reported as added. [F02]
- The dependency comparison also reads what `pyproject.toml`, `setup.py` or `setup.cfg`
  declares when a project installs from requirement files or a lock; metadata that cannot be
  read statically, with no lock, is a missing item. [F02]
- `forbidden_paths`: a pattern that matches a directory forbids everything under it
  (`migrations`, `migrations/`); a pattern that could never match (a leading `/`, a backslash,
  a `..` segment) is a `repro.yml` error with its line. [F02]
- Invariants `no_network` and `perf_budget`: head's network attempts past this machine (the
  sandbox's detections; loopback and Unix sockets are local) fail `no_network`, and a pass
  states whether the network was enforced (Docker) or only watched, with a
  `network_not_enforced` item for the latter. A timed test that D07 calls a regression with
  its interval past `max_regression` fails `perf_budget`. Fixture `net-call`; `perf-regress`
  and `invariants-pass` enable them. [F03]
- The status engine: `VERIFIED`, `FLAGGED` or `UNVERIFIABLE` by deterministic rules over a
  run's event log, with the reasons and the evidence each rests on. A difference no claim
  explains flags; anything not checked (an invariant not evaluated, a job that did not
  complete, too many changed symbols unverified) leaves the change unverifiable. The policy
  in force is now `policy` evidence on every run, so a replay decides the same. [F04]
- The Reproduction Card: every run ends with a card stage that assembles the card from the
  run's own log (status and reasons, conditions, gap, invariants, missing items, replay
  digest), publishes it in `card.published` and stores it; `run.finished` carries its status.
  `repro export RUN|LOG --format md|json` writes it, refusing a card that differs from the one
  published, and `repro replay` checks it. The cockpit opens it from a header button. [F05]

## [0.5.0] - 2026-10-15

Stage E — Behaviour evidence. Gate record: `docs/phases/E-gate.md`.

### Added
- The tracer: for each function a change touches, every call in each test, its arguments read
  before the body runs, its result, exception or yields, as stable digests (the same under any
  hash seed and on Python 3.12 to 3.15), attributed to the test and its phase. 200 calls per
  test and function, then the function is switched off completely; a run with no free
  `sys.monitoring` tool ID, or past the budget, says so as a missing item. It runs beside
  coverage.py. A benchmark harness (`scripts/bench_tracer.py`) tracks its overhead. [E01]
- The canonical encoder: objects by their type and state, builtin subclasses (named tuples and
  the like) by their value and state, exceptions by type, arguments and cause chain, standard
  library values (dates, decimals, paths, UUIDs, patterns, enum members) by their canonical
  text, functions and modules as named tokens; directories and addresses normalised; for
  results, a second digest with dictionaries sorted, so a change in order alone can be told
  apart. No code of the target runs to encode a value: everything is read through the
  interpreter's own descriptors. Hypothesis property tests and a corpus of hostile objects
  check it. With it, the tracer's benchmark stays at 1.01–1.18x on Python 3.12 to 3.15; call
  records are flushed once per test. [E02]
- Behaviour observations: the base runs (A, A2) and the head run (H) trace the functions a
  change modified, and their calls are joined across runs by test, function, arguments and
  occurrence. What differs between the two base runs (another test order, hash seed and random
  seed) is masked, per call and field, so time, randomness and hash order never become a
  difference; what could not be compared is a missing item. Each run's observations and the
  comparison are stored as deterministic CBOR artifacts (RFC 8949), read back only in that
  form. A `noisy` fixture. The trace has its own capped file in the sandbox. [E03]
- Behaviour differences as evidence: each call that behaves differently on head for the same
  input is classified (another result, an exception added, removed or changed, its order only,
  its input mutated, or other calls made), grouped per test and function, run again on both
  sides to confirm it, and shown with a reproducer (the test, the call and what each side gave).
  Each group is `behaviour_diff` evidence with a `diff.behaviour` check. The tracer encodes a
  call's arguments again when it ends, to see them mutated. `calls_changed` joins the schema's
  behaviour classes. Fixtures `silent-change` and `false-claim`. [E04]
- API and dependency differences: the public API's breaking changes (griffe, reading files
  only, plus changed return and attribute annotations, which griffe does not check), and the
  dependencies added, removed, upgraded or downgraded, as locked or declared and as installed
  on each side, with lock drift. Each is evidence with a `diff.api` or `diff.deps` check.
  `diff.deps` sources gain `declared`. Fixtures `api-break` and `new-dep`. New dependencies:
  `griffelib` 2.3.2, `packaging`. [E05]
- Generated input probes: the changed functions whose parameters are JSON types are called with
  40 Hypothesis-generated inputs each (the same every run) on base, base again with other seeds,
  and head, inside the sandboxes; inputs on which base disagrees with itself are left out, and
  the rest that differ between base and head are `probe_diff` evidence (`provenance: generated`)
  with a `diff.probe` check, the input as the reproducer. Timing now runs after the other runs.
  Fixture `probe-only`; Hypothesis becomes a runtime dependency. [E06]
- The cockpit's behaviour field and Signals from real runs: the traced runs preview each test's
  first four calls of each changed function, sent as `trace.sample` events while the run goes.
  Gate E's browser test (`gate-e.spec.ts`, CI job `gate E`): an output change no test checks
  shows as two stable behaviour diffs with their reproducers, and A/A′ noise as unstable cells,
  never a diff. [E07]

## [0.4.0] - 2026-10-15

Stage D — Sandboxed two-sided execution. Gate record: `docs/phases/D-gate.md`.

### Added
- Gate D's browser test (`apps/web/gate/gate-d.spec.ts`, CI job `gate-d`): Repro's fixtures through
  `repro run` in full, read in the cockpit's Execution, Change routes and index. The cockpit's
  test hook summarises those panels from their own views. A fixture `breaks-one`, and
  `python -m fixtures` to build any fixture repository. [Gate D]
- The process sandbox (L0 to L2): a command runs with a minimal environment, Repro's audit
  hook in every Python process it starts, and containment: a Job Object on Windows (created
  suspended, assigned, resumed; job-wide memory, a process limit, kill-on-close), a process
  group with resource limits on POSIX. A wall-clock timeout kills the whole tree; processes left
  behind are killed and reported. Network, subprocess and file-write attempts become evidence
  (`subprocess_attempt` and `file_attempt` are new evidence types). `sandbox.ready` states the
  level reached, with the network detected only. `repro doctor` reports containment. [D01]
- The container sandbox (L3): the commit is streamed from its tree object into a Docker volume
  of its own, and each command runs in a fresh container with no network, a read-only root, an
  `exec` tmpfs at `/tmp`, no capabilities, no new privileges, uid 1000, and process, memory and
  CPU limits; only Repro's audit hook is mounted, read-only. The escape suite (raw `_socket`,
  `ctypes`, a subprocess reaching out, writes outside the work directory, the host's `.git`) is
  blocked, and `sandbox.ready` reports the network `enforced`. `repro doctor` reports the level
  runs reach. [D02]
- Environment provisioning: the project's kind (uv, poetry, pdm, requirements, setuptools) and
  requirements are read from its files, refusing anything but the package index. The
  dependencies are installed from wheels only, before any target code runs, into an environment
  cached by its digest and shared by both sides and later runs; the project is then built
  offline in the sandbox. Five fixture projects provision on Windows and Linux, on the host and
  in containers, and their tests pass in the result. A failed install is an `environment_failed`
  item with uv's reason. Repro's sandbox image adds `less`, `git`, `procps` and `tzdata`, with
  uv 0.13.0. [D03]
- Repro's pytest plugin and test command: each test's outcome (`passed`, `failed`, `error`,
  `skipped`, `xfailed`, `xpassed`), the phase where it did not pass and why, written to a results
  file as the run goes and read back as untrusted input; a run that dies or times out is
  incomplete, never passing. The project's `addopts`, random order and xdist are overridden, a
  module that fails to import no longer hides the other tests, `random` is seeded per test, time
  zone and locale are pinned, and a network guard fails non-loopback connections at once and
  records them. The plugin's suite passes on Python 3.12, 3.13, 3.14 and 3.15, and in a
  container. [D04]
- Test selection: the plugin records the functions each test runs (`sys.monitoring`, once per
  function and test), the engine turns them into symbols and selects the tests a change can
  affect: those that ran a changed function on base, those importing a module-level change or
  below a changed `conftest.py`, and the tests the change adds or edits, each with its reasons.
  Changed symbols no selected test runs, on either side, become `no_covering_test` items. Base
  maps are cached; the map is the same on the host and in a container. New fixtures `selection`
  and `untested`. [D05]
- `repro run` runs the change: each side in its sandbox (Docker when available) with its
  environment, the selected tests on base twice (the second time in reverse order with another
  hash seed) and on head, flips rerun three times on both sides, and edited tests' base versions
  run against head. Each test gets its outcome class (`F2P`, `P2P`, `P2F`, `F2F`, `new`,
  `removed`, `modified`, `flaky`) as evidence, and changes to the tests themselves (removed tests,
  new skips and xfails, removed or weakened assertions, widened `pytest.raises`, base tests
  failing on head) as evidence too. Flaky tests, unfinished runs and collection errors are
  missing items; an environment that cannot be built makes the change unverifiable, not the run
  fail. `--analyse-only` keeps the build stage alone. New fixtures `flaky` and `test-tamper`. [D06]
- The performance pass: the tests that pass on both sides are timed untraced, base and head
  interleaved (ABBA), ten runs a side; each gets the ratio of medians with a BCa bootstrap 95 %
  interval, and is a regression or an improvement only if the interval excludes 1, the change
  clears the noise floor (10 %, or the A/A spread measured in the same runs if larger) and a
  Mann-Whitney test stays significant across tests (Benjamini-Hochberg). Tests too fast to time
  are listed as not assessable. New fixture `perf-regress`. [D07]

### Fixed
- The repeat base run (A2) reported its tests on the `base` side; it now uses the schema's
  `base_repeat`, so the cockpit's Execution panel shows it apart from base. [D07]
- The run stage's check events now carry the statuses ADR-0015 defines: a P2F and every
  test-tampering finding are `fail`, not `warn`, so the index flags them without waiting for
  review. [D07]
- Two builds of the same cached dependencies' environment in containers at once (both sides
  of a run, or two runs) wrote into one volume and failed; each build now has its own. [D06]
- A snapshot that left entries out (a symlink, a submodule) failed its job: the warning
  it logged had a level the event schema does not allow. Log levels are now checked by type. [D06]
- Evidence could not be emitted through the event bus: it validated `evidence.created` events
  without the evidence's `seq`, which only the store's writer assigns. [D01]

## [0.3.0] - 2026-10-14

Stage C — Engine core & live stream. Gate record: `docs/phases/C-gate.md`.

### Fixed
- The event stream could crash the engine (a segmentation fault, seen once on the Ubuntu runner):
  a client leaving mid-replay cancelled the handler while a SQLite read still ran on a worker
  thread, and the cleanup closed the same connection from another thread. Each stream's reader
  now lives on one thread of its own, so the close queues behind any read in flight. [C05]

### Added
- `repro doctor`: checks Python (3.12+), git (2.40+), uv (the 0.13 baseline), Docker (daemon,
  Linux containers, kernel), the data directory and, on Windows, the run root's length and
  long-path support. Each check is ok, warn or fail with a hint; `--json` prints a report; exit
  status 1 when a required check fails. It does not change the machine. [C01]
- Engine settings from flags, `REPRO_*` variables and a TOML file, validated, with errors naming
  their source; default data paths per platform (`~/.repro`, a short run root under
  `%LOCALAPPDATA%` on Windows); structured JSON logs on stderr with credentials redacted. [C01]
- `repro run`, `replay`, `serve` and `export` are declared with their options and exit with
  status 2, naming the phase that brings them. [C01]
- The store: SQLite (WAL, STRICT tables, numbered migrations) for the run registry, events,
  evidence, artifacts and cards. The schema makes events and evidence append-only and `seq`
  gap-free per run, and ties evidence to its `evidence.created` event. A single writer thread
  batches events into `BEGIN IMMEDIATE` transactions, assigns `seq`, and publishes only after
  the commit; a failed batch is retried group by group. Content-addressed artifacts are written
  atomically and confined to their run's directory. ULIDs with the schema's prefixes; `repro
  doctor` also checks SQLite (3.37+). 100k events round-trip in order, and a writer killed
  mid-transaction or at random leaves a consistent database. [C02]
- The event bus: builds envelopes (ULID, time, `v`, the kind's `pv`, producer), scrubs GitHub
  tokens, AWS access key IDs, PEM private keys and `Bearer` credentials, validates each event
  against its kind's generated model, and publishes to subscribers only after the commit. [C03]
- The orchestrator: a run's jobs as a DAG on daemon threads with a concurrency limit, per-job
  timeouts and cancellation (subprocess trees are killed). Failed, timed-out and skipped jobs
  become `fail` events and missing items; `stage.started`/`stage.finished` frame each stage; and
  `run.finished` (outcome, duration, counts) is always emitted, followed by the run registry and
  a WAL checkpoint. A golden log pins a stub pipeline. [C03]
- The API (`repro serve`, FastAPI and uvicorn): read-only REST for runs, evidence, verified
  artifacts and cards, and a WebSocket that replays a run then follows it live, every event
  exactly once in `seq` order (subscribe first, replay in pages, deduplicate, `caught_up`). A
  client that falls behind is closed with "resync" and resumes losslessly with `?from=`; the
  writer never waits for clients. Local only: requests must name a local host (DNS rebinding),
  WebSocket origins must be allow-listed (cross-site WebSocket hijacking, THREAT_MODEL T16), and
  `repro serve` listens on loopback only. [C04]
- The git layer: hardened git commands (long paths; the repository's hooks, fsmonitor and diff
  drivers never run), ref resolution and merge bases, diffs with exact paths from `--raw -z`
  and changed line ranges from `-U0`, `.git`-less snapshots written byte for byte from the tree
  object (not `git archive`, which honours `export-ignore`), with unsafe entries skipped and
  listed, and worktrees whose cleanup waits out Windows file locks. [C05]
- Changed symbols: each file's module name from its packages (`__init__.py`), a symbol table
  from `ast` (functions, methods, classes and module code, with stable `module:qualname#ordinal`
  IDs), and, for two commits, the symbols that were modified, renamed, moved, added or removed,
  as `symbol.changed` payloads. A symbol is modified only if its own code changed: comments,
  reformatting and edits to nested definitions do not count against it. Overloads and other
  repeated names are aligned by code, renames and moves matched by AST similarity, and a file
  whose package changed above it moves with it. Files that do not parse, are too complex to
  parse or have no importable module name are reported, never guessed. [C06]
- Import graphs and blast radius: each side's module import graph, from grimp for packages and
  from `ast` for test files and scripts outside packages, and the blast radius of a change: the
  modules importing its symbols' modules, directly or not, with their distance, over both sides'
  imports. grimp runs in a process of its own and never resolves a target name through
  `sys.path`, so no target code runs whatever the package names (THREAT_MODEL T19). Unparsable
  files, unsafe paths and a slow grimp are reported. `graph.node`, `graph.edge` and
  `blast.computed` payloads. New dependency: grimp 3.17 (BSD-2-Clause). [C07]
- `repro run --repo <path> --base <ref> --head <ref>`: reproduces a change's build stage
  (snapshots of both sides and the static analysis) and shows it in the cockpit live, from the
  same process; `--no-serve` waits and exits 0 or 1. `repro replay <run id | log.jsonl>` shows
  a stored run or a validated event log from the start; `repro serve` serves the built cockpit
  beside the API (`ui_dir` setting). What the analysis could not read is a `missing.recorded`
  item. Snapshots name their content (`tree_sha256`). [C08]
- The cockpit follows the engine's event stream: `?run=<id>` live, `?run=<id>&replay` from the
  start, or the engine's newest run. Gaps and resyncs are recovered from the engine's store, lost
  connections retried, refusals reported; a finished live run ends in the same state as its
  replay. Gate C's end-to-end test runs in CI on pallets/itsdangerous. [C08]

## [0.2.0] - 2026-10-14

Stage B — Cockpit on fixtures. Gate record: `docs/phases/B-gate.md`.

### Added
- Model coverage gate: projections and store at least 90% (lines, statements, functions) in CI
  (`pnpm coverage`, SDLC.md §5). [Gate B]
- `@repro/synth`: a deterministic synthetic-run generator (seeded sfc32, ULIDs, single-writer
  ordering) with `DEMO` and `STRESS` profiles. Committed fixtures
  `apps/web/fixtures/demo-run.jsonl` (2,334 events, 28 checks, 4 flagged) and
  `stress-run.jsonl` (18,542 events, 10,000 edges, a 3k-event one-second burst), a drift test,
  and `pnpm fixtures`. [B01]
- ADR-0015, the check model: which events are checks, what their status means, and how
  verdicts resolve pending checks. It defines the CHECKS and FLAGGED counters and the
  change×check index. [B01]
- `@repro/events/testing` export (Ajv harness) for other packages' tests. [B01]
- Cockpit data layer (`apps/web/src/model`): a columnar `EventStore` with gap and duplicate
  handling, `Folder` snapshots with exact seek, `WindowIndex` and `KindIndex`, a
  `PresentationClock` (replay ×0.5–×16, live, global pause), and the `run`, `summary`,
  `checks` (ADR-0015), `claims` and `agents` projections behind one cockpit state. [B02]
- Cockpit shell (Preact 11 + signals): a header with five counters and a 7-stage progress bar,
  a stage frame, and eight panel frames. Geometry comes from a single `computeLayout` (desktop
  and narrow rules), the frame loop re-renders only on new information, and the cockpit replays
  the demo fixture (`?log=stress` for the stress run). [B03]
- Design tokens from DESIGN.md and self-hosted JetBrains Mono (variable woff2), with the OFL
  licence emitted next to the font files. [B03]
- WebGL stage foundation: `StageHost` (three.js `WebGLRenderer`), pure `StageLayer`s with
  preallocated instance buffers uploaded only on change, an instanced anti-aliased segment
  primitive (GLSL ES 3.00), adaptive DPR, allocation-free frame statistics and a perf HUD
  (`?hud`). [B04]
- GPU-free render-model harness (`hashRenderModels`) and a stress benchmark (`bench.html`) with
  a pixel sanity check. On an integrated GPU, 10k edges and 2,048 streaks hold 60 fps at DPR 2.
  Results are in `docs/perf/`. [B04]
- Dependency web on the stage: `GraphIndex`, a deterministic d3-force layout in a worker
  (containment links, radial equalisation, cached by graph hash), static edges revealed and
  flared by a shader recency term, blast-radius markers, and event-triggered Bézier bundles. On
  an integrated GPU, 10k edges hold the frame budget at DPR 2. [B05]
- Ring of file cards: one instanced draw call over a text atlas drawn in a worker
  (OffscreenCanvas → ImageBitmap). Each card is built from the events about its file (path,
  imports, symbols, changes). The rotation is closed-form, with 500 ms ease-out-cubic snaps to
  the focused file, plus a tick scale and rims. Segment layers can now turn in the shader with
  no per-frame uploads. [B06]
- Event streams: up to 2,048 live streaks on per-file lanes, with eased heads,
  velocity-stretched tails and DESIGN.md state tints, as a pure function of (events, t). Plus
  callouts: at most two bordered boxes naming the latest findings. With every stage layer real,
  the stress burst holds the frame budget on an integrated GPU. [B07]
- Canvas 2D panels: investigation feed (150 ms row insert), execution lanes (BASE with the A′
  repeat run, HEAD, and a BASE · HEAD · BOTH toggle), agent workspace (typed tool calls, block
  cursor while working), and behaviour diff field (A/A′/H agreement per test × symbol, module
  histogram). Each panel is a pure view plus draw, redrawn only when its version changes, and is
  covered by golden draw-call recordings. `TestIndex` and `ObservationIndex` join the cockpit
  model. [B08]
- The second row of panels: change routes (FILE · SYMBOL · TEST · CHECK with progress pills),
  signals (pass rate, behaviour drift and perf Δ with check ticks; "illustrative until run
  completes" while partial), agents / dispatch (12 slots, segmented bars, flagged agents), and
  the change×check index (claims and unclaimed changes × every check, with row status). A
  `ChangeIndex` routes selected tests to changed symbols through `calls` edges and module
  imports, using only the log before each selection. The index counts the same checks as its
  footer and the header. [B09]
- Replay transport along the bottom of the stage: Play/Pause, ×0.5–×16, a scrubber, Live and
  Reduce motion. Space pauses everything except a focused control. [B10]
- Motion safety (ADR-0011): every visual duration is wall-clock at any replay speed, and the
  stage, panels and callouts are pure functions of (events, t, motion). A flash governor keeps
  the bright area that rises in any 333 ms under half of WCAG 2.3.1's 87,296 CSS px² flashing
  area, at every speed, on both fixtures. `prefers-reduced-motion` and the in-app toggle stop
  streaks, bundles, flares, drift and panel animation. [B10]
- Focus: click or keyboard on index rows and cells, route rows, dispatch slots, ring cards and
  web nodes. The stage outlines the focus and pins its file on the ring, the agent workspace
  follows a focused agent, and an evidence drawer shows the claim's verdict text and quotes,
  the check's evidence and trace samples, the file's changes, routed tests and related checks.
  [B11]
- Accessible parallel DOM: a throttled `role="log"` for the feed (at most one summary every 2 s),
  semantic tables for the index and the drawer, button lists for routes and agents, a file
  picker, a described stage canvas, and landmarks. axe-core 4.14.0 reports no violations in
  jsdom (CI) and in Chromium. [B11]
- UI quality gates in CI. Each one rejects an injected regression of its kind in a self-test.
  [B12]
  - Visual regression: Playwright 1.64.0, Chromium and SwiftShader in an image pinned by digest,
    baselines generated in CI, a `renderAt(t)` hook.
  - Allocation: a flat heap over 1,000 frames of the stress burst, and a per-frame stage budget.
  - Size: size-limit, 300 kB gzip; currently 188.5 kB.
  - Perf: a calibrated frame-cost gate against a CI baseline, also nightly.
- Visual parity with the reference. [B13]
  - The ring's arc spans the stage height (radius 0.9·H, 3 o'clock point at 0.46·W).
  - At most 64 larger file cards, with legible 4–5 px text.
  - Stream lanes within the visible arc (±28°).
  - A stream residue: each event's faint, static trace along its lane, faded by the shader over
    90 s of run time.
- Reference parity, second pass, against a sharper copy of the reference (1080×1350), at the
  owner's Gate B review. [B14]
  - Layout: on landscape screens the eight panels sit beside the stage in 4 rows × 2, so the
    stage keeps the reference's near-square shape (904×844 at 1440×900). Portrait screens get the
    reference's own arrangement, with the stage on top.
  - Ring: geometry measured on the reference (inner radius 1.02·H, band 0.215 of it). It is an
    opaque drum with a lit bezel and ruler combs. Code cards fill the band at their natural size:
    a `// package / module` header, numbered rows of symbols and imports, a neighbour dot grid and
    labels.
  - Web: a chord layout, with modules on a circle under the band in the force simulation's order
    and edges as arcs, so the stage shows the reference's hairball of long curves. The run's
    nodes, symbols and tests, sit in front as small circles.
  - Strands (bundles): when an event names a function, a test or a file and its section of the
    wheel (its file's card) is on screen, a bunch fires there. It holds one dim strand from each
    of the nodes connected to it nearest that section (a function's tests and callers, a test's
    functions, a file's own nodes, from the log so far; at most 8), with a white bullet shooting
    along each. The strands converge side by side on the section, stay
    attached to their nodes, follow the section as the ring turns, and fade after 1 s. The ring
    turns once per 24 s of wall-clock time, the reference's measured speed. The mesh's arcs are
    curvier and dimmer, and none leaves the wheel.
  - Nodes (symbols and tests) are scattered across the whole visible part of the web by one
    placement rule: height by the simulation's order, depth by role (tests deeper in).
  - Lines outside the wheel answer a hit: when a bunch lands, its section's recent events shoot
    out from the outer rim at their places on that section, then rebound into the rim and vanish
    (a bounce, 0.6 s), turning with the wheel. Nothing leaves the wheel without a hit; the
    per-event streaks (B07) and the long-lived rays (B13's residue) are removed.
- Stage settings and the whole wheel, at the owner's seventh B14 review. [B15]
  - A Settings menu (in the transport) with a thickness and a brightness slider for strands,
    bullets, mesh, nodes and lines outside, and a reset; kept per viewer in the browser. The
    strands' and lines' brightness can only go down from their flash-safe ceiling; the flash
    governor counts bullets and mesh flares at the chosen width.
  - A view from the reference's arc out to the whole wheel (Arc, Whole wheel, a zoom slider).
    Every layer, the governor, hit tests and callouts follow it; the mesh dims as it crowds a
    smaller disc, and card text is mipmapped.
  - The run's nodes are placed over the whole disc: angle by the simulation's order, distance
    from the centre by role.
  - Callout boxes stay inside the stage; zoomed out they were cut off at its right edge.

### Fixed
- The feed and execution panels could keep showing a stale drawing between version steps (the
  view moved continuously while the version stepped). Both now quantise like their versions, and
  `versionOf` no longer wraps after 4,096 phases. Found by B10's "scrub = play" property. [B10]
- Panel canvases are cleared in device pixels, so no sub-pixel sliver keeps stale content and
  Chrome can discard pending draw operations. Canvas sides are clamped to 4,096 CSS px. [B09]
- Stage geometry is rebuilt when a layer's buffers grow. three.js caches the maximum instance
  count per geometry and would otherwise draw no more instances than the first capacity. [B05]

### Changed
- The dim strands and the lines outside the wheel blend with MAX at no more than 0.32, under
  WCAG's 10% luminance change, so they are not flashes (ADR-0011 note). The flash governor
  aggregates one bunch per section of the wheel per window. Segment arcs no longer overlap at
  their joints. The B04 proxy pins use a fixed stage, independent of the cockpit layout. [B14]
- `--text-dim` is `#868686` (was the reference's `#6e6e6e`, 3.9:1) to reach WCAG 1.4.3's
  4.5:1. `--text-faint` is no longer used for text, and callouts and the transport are opaque.
  [B11]
- `SchemaSet.event_errors` caches validators and skips the redundant whole-`oneOf` check when
  `kind` discriminates the registry (`SchemaSet.kind_discriminates`). It is about 5× faster
  with identical results. [B01]

## [0.1.0] - 2026-10-13

Stage A — Foundation. Gate record: `docs/phases/A-gate.md`.

### Added
- Project brief, research synthesis, final architecture, stage/phase roadmap, SDLC process,
  ADRs, threat model, test strategy and research-paper tracker. [A01]
- pnpm workspace (`apps/web`, `packages/events`) and uv workspace (`engine`, `bench`) with
  pinned toolchains: pnpm 12.11.2, Node >= 22.12, TypeScript 7.0.2, Vite 8.3.4, Vitest 5.0.4,
  Python 3.13 (requires >= 3.12), `uv_build`. [A02]
- `repro` CLI entry point with `--version`. [A02]
- CI workflow: clean-checkout install, typecheck, tests, build and CLI smoke test on Ubuntu
  and Windows. [A02]
- Apache-2.0 `LICENSE` and `NOTICE`. [A02]
- Quality gates: Ruff (lint + format) and strict mypy for Python (ADR-0013), Biome for
  TS/JSON, `pnpm lint`, an optional local pre-commit config, and a required CI `lint` job. [A03]
- Event schema v1 (JSON Schema 2020-12): envelope with ADR-0003 fields, a registry of 34 event
  kinds with stage and payload-version constraints, payload schemas, and claim, evidence,
  verdict, missing-item, artifact and card models, all with valid and invalid examples. [A04]
- `repro-events` Python package: schema registry, field-precise event validation, and
  draft-07 derivation of agent output schemas. [A04]
- In-house schema codegen (ADR-0014): TypeScript types (`ReproEvent` union, `EVENT_KINDS`,
  `KIND_STAGES`) and strict Pydantic v2 models with a discriminated `Event` union; `pnpm gen`;
  CI drift gate; equivalence tests across JSON Schema, Pydantic and Ajv. [A05]
- CI pipeline: `lint`, `test` (Ubuntu, Windows), `security` (gitleaks over full history,
  licence allow-list), PR dependency review; SHA-pinned actions; Dependabot for actions, npm and
  uv. [A06]
- Test harness: Hypothesis (dev/ci profiles) and fast-check; `repro-events validate` /
  `normalise` CLI for event logs (schema plus log invariants, including evidence-before-citation);
  golden-file fixture; deterministic fixture-repository builder with the `honest-fix` fixture. [A07]
- Feasibility spikes with results (`spikes/A08/`): `sys.monitoring` tracer overhead on CPython
  3.12–3.15 (Windows/Linux), Docker L3 sandbox round trip, and container provisioning of a real
  project. A spike workflow reproduces them on a Linux runner. [A08]

### Changed
- Common enum `Verdict` renamed `VerdictValue` (it collided with the `Verdict` model). [A05]
- Event registry branches declare `"type": "object"` (Ajv strict mode). [A05]
- ADRs 0003 (envelope versioning fields), 0004 (Docker as the default sandbox), 0007 (agent
  runtime) and 0011 (motion safety) accepted. [A02]

[Unreleased]: https://github.com/DuhItzAniket/Repro/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v1.4.0
[1.3.1]: https://github.com/DuhItzAniket/Repro/releases/tag/v1.3.1
[1.3.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v1.3.0
[1.2.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v1.2.0
[1.1.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v1.1.0
[1.0.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v1.0.0
[0.7.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v0.7.0
[0.6.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v0.6.0
[0.5.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v0.5.0
[0.4.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v0.4.0
[0.3.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v0.3.0
[0.2.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v0.2.0
[0.1.0]: https://github.com/DuhItzAniket/Repro/releases/tag/v0.1.0
