# repro-engine

The Repro engine: orchestrator, event bus, storage, sandboxed two-sided execution, behaviour
tracing, diffing, policy, agents and card export. The design is in
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

The import package is `repro` (src layout: `engine/src/repro/`). The console script is `repro`.

```
uv run repro --version
uv run repro doctor          # Python, SQLite, git, uv, Docker and the data paths (--json)
uv run repro run --repo <path> --base <ref> --head <ref>   # the cockpit shows it live
uv run repro run --pr https://github.com/<owner>/<repo>/pull/<n>  # a GitHub pull request
uv run repro replay <run id | log.jsonl>                    # from the start
uv run repro export <run id | log.jsonl> --format md|json|in-toto|sarif|junit  # the Reproduction Card
uv run repro report <run id | log.jsonl>                    # the card on its pull request
uv run repro serve           # the cockpit, the read-only API and the event stream
uv run pytest
```

`run`, `replay` and `serve` listen on `127.0.0.1:8780` (loopback only; `--port`, `--open` to
open the browser, `--allow-origin` for a dev server) and serve the built cockpit (`pnpm build`;
the `ui_dir` setting points elsewhere). `run --no-serve` waits for the run and exits 0 if it
completed, 1 if not. Every run ends with its card (F05): `export` assembles it again from the
log, as Markdown or canonical JSON (`-o FILE` to write a file), and exits 1 if it differs from
the card the run published; `replay` reports the same check before serving. Since H04 it also
writes an unsigned in-toto Statement, SARIF 2.1.0 and JUnit XML. `report` (I02) puts the card,
checked the same way, on the GitHub pull request the run read (`--pr` names another): the
check run `Repro` on its head commit, one comment that each report updates, and, in GitHub
Actions, the job's summary. It runs none of the target's code; a target it cannot update is
named, the others are still made, and it exits 1.

## Store

`repro.store` keeps runs, events, evidence and artifacts in `<data_dir>/repro.db` (SQLite, WAL)
and `<data_dir>/runs/<run_id>/`. `StoreWriter` is the only writer: it batches submitted events
into transactions, gives each its run's next `seq`, and resolves callers' futures after the
commit. `StoreReader` pages through a run's events read-only. `ArtifactStore` writes
content-addressed files atomically. The schema itself enforces append-only events and evidence
and gap-free `seq` (ARCHITECTURE §11).

## Bus and orchestrator

`repro.bus.EventBus` is the one way events enter the store: it builds the envelope, removes
recognised credentials, validates the event against its kind's generated model, and submits it
to the writer; subscribers get events only after they are committed. `repro.orchestrator` runs a
run's jobs as a DAG on daemon threads (at most `concurrency` at once), with per-job timeouts and
cancellation; failures, timeouts and skipped jobs are recorded as `fail` events and missing
items, and `run.finished` is always emitted.

## Git layer

`repro.gitops` reads an untrusted target repository: `Git` runs hardened commands (long paths,
no hooks, no fsmonitor), `resolve` and `merge_base`; `diff` lists changed files with exact paths
and their changed line ranges; `snapshot` writes a commit's tree, byte for byte, into a
`.git`-less directory, skipping and listing entries that are unsafe on this machine;
`add_worktree` and `remove_worktree` give host-side checkouts that are always cleaned up.

## Static analysis

`repro.analysis` reads a target's Python source with `ast`, never importing it.
`module_names` names each file as Python would import it; `build_table` lists a module's
functions, methods and classes with stable IDs (`pkg.mod:Class.method#0`, `<module>` for the
module's own code); `changed_symbols_between(git, base, head)` gives each symbol the change
touched (`modified`, `renamed`, `moved`, `added`, `removed`), as `symbol.changed` payloads, and
lists the files it could not read. `import_graph` builds one side's module import graph (grimp
for packages, in a process of its own; `ast` for tests and scripts outside packages), and
`analyse(git, base, head, workdir)` returns the changed symbols, both graphs and the blast
radius, with `graph_payloads` and `blast.payload()` giving their events.

## Sandbox

`repro.sandbox.ProcessSandbox(work, limits)` runs a command at L0 to L2 (ADR-0004):
`run(argv, cwd=…, env=…, timeout_s=…, stop=…)` returns an `Execution` (outcome, exit status,
output, whether processes were left running, and the network, subprocess and file-write
attempts the audit hook detected). `detection_evidence` turns detections into evidence records;
`ready_payload(side)` is the `sandbox.ready` payload. Containment is a Job Object on Windows and
a process group with resource limits on POSIX; the network is detected, not enforced.
`DockerSandbox(work, git, commit, limits=…)` runs at L3: the commit in a volume of its own, each
command in a hardened container with no network; `read_file` reads a result back.

## Provisioning

`repro.provision.inspect_project(snapshot)` reads a project's kind (uv, poetry, pdm,
requirements, setuptools) and its requirements from its files. `HostProvisioner` (the process
sandbox) and `DockerProvisioner` (containers, in `repro.sandbox.image`'s image) build the
dependencies' environment from wheels, cache it, copy it for the side and install the project
offline in the sandbox; `Environment.payload()` is `env.provisioned`, and a failure is a
`ProvisionError` whose reason `environment_missing` turns into an `environment_failed` item.

## Running tests

`repro.runner.run_tests(sandbox, env, tests=..., seed=...)` runs node IDs (or every test) with
the environment's interpreter, under Repro's pytest plugin (`sandbox/_hook/repro_pytest.py`) and
Repro's own options, whatever the project configures. It returns the execution, the plugin's
results read back as untrusted input (`complete` only with the session record) and the JUnit XML.

## Test selection

`run_tests(..., map_tests=True)` also records the functions each test runs (`sys.monitoring`; a
method a dataclass, attrs or `NamedTuple` writes for a class is recorded as the class);
`repro.runner.selection` turns them into symbols (`test_map`), picks the tests a change can
affect with their reasons (`select`), and lists the changed symbols no selected test runs
(`uncovered`, `no_covering_test`). Base maps are cached (`MapCache`).

## Two-sided execution

`repro.runner.twosided.reproduce` runs A (base), A2 (base, reverse order, another hash seed) and
H (head) on the selected tests, reruns flips, runs edited tests' base versions on head, and
classifies each test (`outcomes`) and each change to the tests (`testdiff`). `repro run` does
all of it in its run and diff stages (`repro.run_stage`); `--analyse-only` stops after the build
stage.

## Performance

`repro.runner.perf.time_tests` times the tests that pass on both sides, untraced, base and head
interleaved (ABBA); `assess` compares each: ratio of medians, BCa bootstrap interval,
Mann-Whitney with Benjamini-Hochberg, a noise floor from the A/A spread, and a timing floor
under which a test is not assessable. `repro run` does it in its `perf` and `perf-diff` jobs.

## Tracing

`run_tests(..., trace=targets)` records every call of the target functions (`TraceTarget`:
file, qualified name, line span) in each test: argument and result digests, exceptions,
generator yields (`sandbox/_hook/repro_trace.py`, `repro_encode.py`). `repro.runner.trace`
builds targets from C06 symbols, groups calls by target, and gives the missing items.
`scripts/bench_tracer.py` measures the overhead.

## API

`repro.api.create_app` serves `GET /runs`, `/runs/{id}`, `/runs/{id}/evidence/{ev}`,
`/runs/{id}/artifacts/{sha256}` and `/runs/{id}/card`, and the WebSocket
`/runs/{id}/events?from=<seq>`: replay, a `caught_up` frame, then live events from the bus, each
exactly once in `seq` order. A client that falls behind is closed with 4000 "resync" and resumes
with `?from=`. Requests must name a local host; WebSocket origins must be allow-listed; and
every request for the engine's data must carry the server's session token, as
`Authorization: Bearer` or, on the stream, `?token=` (H03, `repro.api.token`).

## Settings

Each setting comes from, in order: a command-line flag, a `REPRO_*` environment variable, the
TOML file (`REPRO_CONFIG`, else `config.toml` in the data directory), the default.

| Setting | Variable | Default |
|---|---|---|
| `data_dir` | `REPRO_DATA_DIR` | `~/.repro` (`%USERPROFILE%\.repro`) |
| `run_root` | `REPRO_RUN_ROOT` | `%LOCALAPPDATA%\repro\r` on Windows, `<data_dir>/r` elsewhere |
| `log_level` | `REPRO_LOG_LEVEL` | `info` (`debug`, `info`, `warning`, `error`) |
| `log_format` | `REPRO_LOG_FORMAT` | `json` (one object per line on stderr), or `text` |
| `concurrency` | `REPRO_CONCURRENCY` | the CPU count, at most 4 (1–64) |
| `sandbox` | `REPRO_SANDBOX` | `auto` (`docker`, `process`; `e2b`: a microVM per side in a self-hosted E2B, L5, K09, with the `e2b` extra and E2B's own `E2B_API_KEY` and `E2B_API_URL`) |
| `e2b_memory_mb` | `REPRO_E2B_MEMORY_MB` | 1024: an E2B microVM's memory, which is its tests' limit (K09) |
| `e2b_cpus` | `REPRO_E2B_CPUS` | 2: an E2B microVM's CPUs (K09) |
| `ui_dir` | `REPRO_UI_DIR` | this checkout's `apps/web/dist`, if built (`pnpm build`) |
| `agents` | `REPRO_AGENTS` | `off`: deterministic only (`claude`, `groq`, `openai`) |
| `agent_model` | `REPRO_AGENT_MODEL` | the backend's per-role models (`repro.agents.setup`) |
| `agent_base_url` | `REPRO_AGENT_BASE_URL` | none; required with `agents = openai` |
| `agent_api_key_env` | `REPRO_AGENT_API_KEY_ENV` | none: an `openai` endpoint that takes no key |
| `agent_mode` | `REPRO_AGENT_MODE` | `live` (`record`, `replay`: cassettes, G02) |
| `agent_cassettes` | `REPRO_AGENT_CASSETTES` | none; required with `record` or `replay` |
| `agent_budget_usd` | `REPRO_AGENT_BUDGET_USD` | `2.0`: the most a run's agent calls may cost together |
| `test_timeout_s` | `REPRO_TEST_TIMEOUT_S` | `900`: each sandboxed test run's wall-clock limit, in seconds (H01) |
| `memory_limit_mb` | `REPRO_MEMORY_LIMIT_MB` | `4096`: the memory a sandboxed run's processes may use (H01) |
| `github_api_url` | `REPRO_GITHUB_API_URL` | `https://api.github.com`: GitHub's REST API for `repro run --pr` (I01); a GitHub Enterprise Server's is `https://<host>/api/v3` |
| `docker_runtime` | `REPRO_DOCKER_RUNTIME` | unset: Docker's default. `runsc` runs the target's containers under gVisor, isolation level L4 (H03); `kata` runs each in a microVM of Kata Containers, L5 (K10; set up with `scripts/kata/setup.sh`) |
| `docker_context` | `REPRO_DOCKER_CONTEXT` | unset: Docker's own. The Docker context every container command uses: a team's engine on another machine (`docker context create builder --docker host=ssh://user@host`), K05 |
| `sandbox_image` | `REPRO_SANDBOX_IMAGE` | unset: Repro's own image and environment. An image pinned by digest (`name@sha256:...`) whose own Python environment the tests run in, as it is: a benchmark task's image, or a CI image (J04). Needs the Docker sandbox |
| `agent_probes` | `REPRO_AGENT_PROBES` | `true`: with agents on, the probe writer proposes inputs for the probes (G07) |
| `agent_order_swap` | `REPRO_AGENT_ORDER_SWAP` | `contradicted`: which verdicts a second reviewer judges with the evidence in the opposite order (`all`, `off`; G08) |
| `agent_panel` | `REPRO_AGENT_PANEL` | empty: three models, comma-separated, that each judge every claim; a verdict stands when two agree (G08) |
| `app_repositories` | `REPRO_APP_REPOSITORIES` | empty: `owner/repo` patterns, comma-separated, narrowing the repositories the GitHub App runs on (I04) |
| `app_concurrency` | `REPRO_APP_CONCURRENCY` | 1: the GitHub App's runs at once (I04) |

With agents on, `repro run` reads the pull request's claims from `--pr-title` and `--pr-body`
(or `--pr-file`: the title, then the description), else from the commit messages. Groq's key is
read from `GROQ_API_KEY`, Claude's from the environment the Agent SDK reads.

Unknown keys and invalid values are errors that name their source. Paths must be absolute
after `~` is expanded.
