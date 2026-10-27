# Contributing to Repro

Repro is developed phase by phase against a public roadmap. Before you open a PR:

1. Read [`docs/process/SDLC.md`](docs/process/SDLC.md). It covers the phase lifecycle,
   Definition of Done, branching and commit conventions.
2. Find the phase you are working on in [`docs/ROADMAP.md`](docs/ROADMAP.md) and create its
   phase record from [`docs/process/PHASE_TEMPLATE.md`](docs/process/PHASE_TEMPLATE.md).
3. Work on a branch `phase/<ID>-<slug>`. Use Conventional Commits that end with the phase ID,
   e.g. `fix(engine): handle detached HEAD in worktree manager [C05]`.
4. Open a PR using the template. `main` is protected: changes land only through PRs that pass
   the required checks (`lint`, `test (ubuntu-latest)`, `test (windows-latest)`, `security`) on
   an up-to-date branch. Merges are squash-only, which keeps history linear.

## What CI checks

| Job | Checks |
|---|---|
| `changes` | Lists the files a pull request changes, and decides whether they are documentation only (below) |
| `lint` | Biome, `tsc --noEmit`, Ruff lint + format, mypy (strict), generated-code drift |
| `test` (Ubuntu, Windows) | Vitest, the web build, pytest (on Ubuntu with Docker required, for the container escape suite), CLI smoke test; on Ubuntu also the bundle size and the model's coverage |
| `agent evals` | The reviewed pull requests replayed from their cassettes, held to `bench/data/thresholds.toml`; no model is called |
| `chaos` (Ubuntu, Windows) | Each failure in [`docs/FAILURES.md`](docs/FAILURES.md), met for real by `repro run` |
| `gate C`, `gate D`, `gate E` | Stage gates end to end in the cockpit (Playwright): a real repository live and replayed; the fixtures in containers; a behaviour diff with its reproducer, and no noise from the A/A2 control |
| `visual` | The cockpit at frozen replay moments against committed baselines, and an injected visual change must fail |
| `gates` | The size, allocation and frame-cost gates each reject an injected regression |
| `security` | gitleaks over the full history; licence allow-list for every Python and JS dependency |
| `install` (Ubuntu, Windows) | The release's wheels built, installed as the README says, and a first run, inside ten minutes |
| `dependency audit` | Known vulnerabilities in every locked dependency (pip-audit, `pnpm audit`); not required |
| `gvisor` | The container sandbox under gVisor (L4), escape suite included; not required |
| `kata` | The container sandbox in Kata microVMs (L5), escape suite included, on GitHub's nested virtualization; not required |
| `e2b` | The E2B sandbox on real Firecracker microVMs (L5), E2B's self-hosted stack started from a pinned commit; not required |
| `dependency review` (PRs) | Known vulnerabilities (high and above) in dependencies the PR adds or changes. Off until the repository's Dependency graph is enabled and the variable `DEPENDENCY_REVIEW=true` is set |

**Documentation only.** A pull request whose every file is documentation that nothing builds,
tests or packages takes a fast path: `lint` and `security` run, the two `test` checks pass with
their steps skipped, and the other jobs are skipped. It finishes in a few minutes instead of
about half an hour. Documentation here means the root documents (`README.md`, `CHANGELOG.md`,
`CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CLAUDE.md`, `CITATION.cff`), text and
pictures under `docs/`, and the issue and pull request templates. It does not
include a document a test reads (`docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`), the packages'
READMEs and PyPI pages, the agents' prompts, or anything else; `scripts/ci_changes.py` has the
rules and `engine/tests/test_ci_changes.py` checks them. Every push to `main` runs everything.

A nightly workflow runs the frame-cost gate, the full visual suite, and the scale benchmark on
three real repositories at pinned commits. A published release builds and attaches its wheels
(`release.yml`).

All actions are pinned to commit SHAs. Dependabot proposes weekly updates for actions, npm and
uv dependencies.

## Dependencies and licences

New dependencies must be under a licence in the allow-list in `scripts/check_licenses.py`
(permissive licences, plus MPL-2.0 for unmodified use and OFL-1.1 for fonts). Record each new
dependency with its version and licence in the phase record.

Ground rules that are easy to get wrong:

- **The event schema is the contract.** Change `packages/events` first, regenerate types,
  then update producers and consumers.
- **Target repositories are untrusted code.** Never run them outside the isolation layer.
- **Nothing is silently unverified.** If a check could not run, it is reported as `missing`.
- **No secrets or personal data** in code, fixtures, logs or commit metadata.

Development commands are listed in [`CLAUDE.md`](CLAUDE.md) ("Commands").
