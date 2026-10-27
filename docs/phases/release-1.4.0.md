# Release 1.4.0 — L5 microVMs and the GitHub App

| | |
|---|---|
| **Release** | `v1.4.0` |
| **Date** | 2026-10-22 |
| **Kind** | A minor release: two new features, no breaking change |

## What it carries
| Part | PRs | What it brought |
|---|---|---|
| K10 | [#116](https://github.com/DuhItzAniket/Repro/pull/116) | Isolation level L5: `docker_runtime = "kata"` runs each container in a Kata Containers microVM with Cloud Hypervisor, the level probed from the container's kernel; `scripts/kata/setup.sh`; the CI job `kata` on GitHub's nested virtualization |
| I04 | [#117](https://github.com/DuhItzAniket/Repro/pull/117) | `repro app register` and `repro app serve`: Repro as a GitHub App on the operator's machine, through a smee.io relay or a port of its own (ADR-0019) |

## Compatibility
- The event schema gains values, not fields: `L5` as an isolation level and `kata` as a sandbox
  backend. A log written by 1.3.x reads as before. A reader older than 1.4.0 refuses a log
  that names `L5` or `kata`.
- Nothing changes for a run without `docker_runtime = "kata"` or the App.

## Published
- GitHub release [v1.4.0](https://github.com/DuhItzAniket/Repro/releases/tag/v1.4.0), 2026-10-22. The release workflow
  ([run 39833178300](https://github.com/DuhItzAniket/Repro/actions/runs/39833178300)) built both wheels, installed them and made a
  first run, then attached them to the release.
- PyPI, through trusted publishing on 2026-10-22:
  [repro-events 1.4.0](https://pypi.org/project/repro-events/1.4.0/), then
  [repro-engine 1.4.0](https://pypi.org/project/repro-engine/1.4.0/).
- Zenodo: [10.5281/zenodo.23574560](https://doi.org/10.5281/zenodo.23574560), a version of
  the concept DOI [10.5281/zenodo.23524047](https://doi.org/10.5281/zenodo.23524047).
