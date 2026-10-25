# repro-engine

**Every agent-written change, reproduced and verified before it merges.**

Repro runs both sides of a pull request (base and head) in isolation, records what each side
actually *does*, and checks every behavioural difference against what the pull request *claims*.
The result is a **Reproduction Card**:

- **VERIFIED**: every claim is supported by evidence, nothing changed that wasn't claimed, and
  invariants hold.
- **FLAGGED**: there is a contradicted claim, an unexplained behaviour change, a broken or
  weakened test, or a failed invariant.
- **UNVERIFIABLE**: too much could not be checked, and the card says exactly what and why.

![The Repro cockpit after a run, with its Reproduction Card open: FLAGGED, because to_celsius returns 38 where it returned 37.8, although both tests pass on both sides](https://raw.githubusercontent.com/DuhItzAniket/Repro/main/docs/guide/demo/card.png)

This package is the engine, the `repro` command and the cockpit. It checks Python changes tested
with pytest, and JavaScript or TypeScript changes tested with Vitest or Jest.

## Install

Repro needs [uv](https://docs.astral.sh/uv/) and git. Docker is optional: with it, the target's
code runs in a container with no network (isolation level L3).

```bash
uv tool install --python 3.13 repro-engine
repro doctor
repro run --repo <path> --base <ref> --head <ref> --open
```

The `e2b` extra (`repro-engine[e2b]`) adds the SDK for a self-hosted E2B sandbox, which runs each
side in a microVM (L5).

## Commands

| Command | What it does |
|---|---|
| `repro doctor` | Says what this machine can run, and at which isolation level |
| `repro run` | Runs a change (`--repo`, `--base`, `--head`) or a GitHub pull request (`--pr`) live in the cockpit |
| `repro replay` | Replays a stored run from its event log |
| `repro export` | Writes a run's card as Markdown, JSON, SARIF, JUnit XML or an in-toto statement, checked against the log |
| `repro report` | Puts the card on its GitHub pull request: a check run and one comment |
| `repro serve` | The cockpit, a read-only API and the event stream, on `127.0.0.1` |
| `repro mcp` | Repro as an MCP server, so a coding agent can request a verification |
| `repro app` | Repro as a GitHub App on your own machine |

## Links

- [Documentation](https://github.com/DuhItzAniket/Repro/blob/main/docs/guide/README.md):
  [quickstart](https://github.com/DuhItzAniket/Repro/blob/main/docs/guide/quickstart.md),
  [reading a card](https://github.com/DuhItzAniket/Repro/blob/main/docs/guide/reading-a-card.md),
  [the GitHub Action](https://github.com/DuhItzAniket/Repro/blob/main/docs/guide/github-action.md)
- [Changelog](https://github.com/DuhItzAniket/Repro/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/DuhItzAniket/Repro/blob/main/SECURITY.md): Repro executes
  the code it verifies, so please report isolation issues privately.
- Cite it through its DOI, [10.5281/zenodo.23524047](https://doi.org/10.5281/zenodo.23524047).

Licensed under the [Apache License 2.0](https://github.com/DuhItzAniket/Repro/blob/main/LICENSE).
