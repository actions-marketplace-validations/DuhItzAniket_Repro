# Repro

**Every agent-written change, reproduced and verified before it merges.**

[![CI](https://github.com/DuhItzAniket/Repro/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DuhItzAniket/Repro/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/repro-engine?label=PyPI)](https://pypi.org/project/repro-engine/)
[![Python](https://img.shields.io/pypi/pyversions/repro-engine)](https://pypi.org/project/repro-engine/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23524047.svg)](https://doi.org/10.5281/zenodo.23524047)

Repro runs both sides of a pull request (base and head) in isolation, records what each side
actually *does*, and checks every behavioural difference against what the PR *claims*. The
result is a **Reproduction Card** with one of three statuses:

- **VERIFIED**: every claim is supported by evidence, nothing changed that wasn't claimed, and
  invariants hold.
- **FLAGGED**: there is a contradicted claim, an unexplained behaviour change, a broken or
  weakened test, or a failed invariant.
- **UNVERIFIABLE**: too much could not be checked, and the card says exactly what and why.

> Not "does it work?", but **"what did it change, and did it say so?"**

![The Repro cockpit after a run, with its Reproduction Card open: FLAGGED, because to_celsius returns 38 where it returned 37.8, although both tests pass on both sides](docs/guide/demo/card.png)

<sub>The cockpit after a run on the `silent-change` example. Both tests pass on both sides, yet
`to_celsius(100)` now returns 38 where it returned 37.8, and nothing explains the change: the
card is FLAGGED. [The demo](docs/guide/demo.md) walks through this run.</sub>

## Quick start

Repro needs [uv](https://docs.astral.sh/uv/) and git. Docker is optional: with it, the target's
code runs in a container with no network (isolation level L3).

```bash
uv tool install --python 3.13 repro-engine
repro doctor
repro run --repo <path> --base <ref> --head <ref> --open
```

`repro doctor` says what this machine can run. `repro run` opens the cockpit, which follows the
run live and ends with the card. A GitHub pull request works too:
`repro run --pr https://github.com/<owner>/<repo>/pull/<n>`.

Repro checks Python changes tested with pytest, and JavaScript or TypeScript changes tested with
Vitest or Jest. To check every pull request of a repository, use
[the GitHub Action](docs/guide/github-action.md) or [the GitHub App](docs/guide/github-app.md),
which runs on your own machine. The [quickstart](docs/guide/quickstart.md) goes from nothing to a
first card.

## Why

Coding agents now open pull requests faster than people can review them. Tests pass, the
description sounds right, and the change still does something else. Published examples:

- About 30% of "plausible" agent patches on SWE-bench Verified behave differently from the
  reference fix ([Wang, Pradel & Liu, ICSE 2026](https://arxiv.org/abs/2503.15223)).
- In agent PRs whose description doesn't match the code, the most common mismatch is claiming
  changes that were never made ([Gong et al., 2026](https://arxiv.org/abs/2601.04886)).

Repro produces the evidence a reviewer needs to trust or reject such a change. Other tools also
execute changes or compare base and head ([research and positioning](docs/RESEARCH.md)). Repro's
focus is evidence at the level of single function calls, a verdict on each of the PR's claims
that cites that evidence, and an explicit list of what could not be checked, all local and
replayable.

## How it works

```
PR ─► READ claims ─► BUILD isolated snapshots + blast radius ─► RUN base ‖ base′ ‖ head
   ─► DIFF outcomes · tests · behaviour · API · deps · perf ─► CHECK invariants
   ─► REVIEW claims against cited evidence ─► CARD (deterministic status)
```

- **Function-level behaviour diff.** Tests that exercise the change run on both sides. A
  `sys.monitoring` tracer records the inputs, outputs and exceptions of affected functions. A
  second base run measures noise, so only stable differences count.
- **Generated probes.** Changed code that existing tests don't reach is exercised by generated
  differential tests.
- **Claims are checked, not trusted.** LLM reviewers may only issue verdicts that cite evidence
  IDs produced by the engine. Uncited verdicts are rejected. The final status comes from
  deterministic rules.
- **Nothing is silently unverified.** Anything Repro could not check appears under *Missing*,
  with a reason.
- **Replayable.** Every run is an append-only event log. The live, animated cockpit and the card
  are pure projections of it. The card exports as Markdown, JSON, SARIF, JUnit XML and an
  in-toto statement.

## Isolation

Target repositories are untrusted code. They run only from snapshots without `.git`, never in
Repro's own tree, and the card states the level each run actually reached.

| Level | The target's code runs | Needs |
|---|---|---|
| L0–L2 | In a separate process with a minimal environment, network and file access recorded, and memory, CPU and process limits | Nothing extra |
| L3 | In a container: no network, read-only root, no capabilities, not root | Docker |
| L4 | As L3, on gVisor's kernel in user space | Docker with gVisor |
| L5 | In a microVM with its own kernel: Kata Containers, or a self-hosted E2B | A host with KVM (WSL2 works) |

Details: [architecture](docs/ARCHITECTURE.md) and [threat model](docs/THREAT_MODEL.md).

## Status

**v1.4.0.** Stages A to I and K are done. The evaluation study (Stage J) has its harness,
protocol and draft pre-registration; it has not run yet. Progress is tracked in the
[roadmap](docs/ROADMAP.md) and every change in the [changelog](CHANGELOG.md).

| Stage | Outcome | Release |
|---|---|---|
| A Foundation | Monorepo, event schema as contract, CI on Windows and Linux | v0.1 |
| B Cockpit | Animated mission-control UI driven by recorded event logs | v0.2 |
| C Engine core | Live graph and blast radius of a real repository | v0.3 |
| D Execution | Sandboxed base/head test runs, outcome and test-tampering evidence | v0.4 |
| E Behaviour evidence | Silent behaviour changes caught without failing tests | v0.5 |
| F Policy & card | Invariants and a complete deterministic card | v0.6 |
| G Agents | Claim extraction, evidence-constrained verdicts, generated probes | v0.7 |
| H Hardening | Installable, documented, hardened | **v1.0** |
| I GitHub | Check runs, sticky PR comments, signed attestations, a GitHub App | v1.1, v1.4 |
| J Evaluation | Benchmark harness and pre-registered study | v1.2 (harness) |
| K Expansion | JavaScript/TypeScript, fleet view, MCP server, symbolic checks, microVM sandboxes | v1.2 to v1.4 |

## Documentation

**Using Repro:** [the guide](docs/guide/README.md): the [quickstart](docs/guide/quickstart.md),
[reading a card](docs/guide/reading-a-card.md), [`repro.yml`](docs/guide/repro-yml.md),
[troubleshooting](docs/guide/troubleshooting.md) and [the demo](docs/guide/demo.md).

**How it is made:** start with [docs/README.md](docs/README.md). Key documents:
[Product](docs/PRODUCT.md) ·
[Research & positioning](docs/RESEARCH.md) ·
[Architecture](docs/ARCHITECTURE.md) ·
[Roadmap](docs/ROADMAP.md) ·
[Design](docs/DESIGN.md) ·
[Threat model](docs/THREAT_MODEL.md) ·
[Decisions (ADRs)](docs/adr/) ·
[Development process](docs/process/SDLC.md)

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md). Repro
executes untrusted code by design: please report isolation issues privately, as
[SECURITY.md](SECURITY.md) describes.

## Citing Repro

Cite the archived release through its DOI,
[10.5281/zenodo.23524047](https://doi.org/10.5281/zenodo.23524047). GitHub's "Cite this
repository" button reads [CITATION.cff](CITATION.cff).

## License

[Apache License 2.0](LICENSE). Third-party components keep their own licences. For example, the
JetBrains Mono font (from Stage B) is under the SIL Open Font License 1.1, and its licence ships
alongside it.
