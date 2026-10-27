# Release 1.5.0 — the E2B sandbox, and runs a killed process left open

| | |
|---|---|
| **Release** | `v1.5.0` |
| **Date** | 2026-10-25 |
| **Kind** | A minor release: one new feature, one fix, no breaking change |

## What it carries
| Part | PRs | What it brought |
|---|---|---|
| K09 | [#119](https://github.com/DuhItzAniket/Repro/pull/119) | `sandbox = "e2b"`: each side of a run in a Firecracker microVM of a self-hosted E2B, at L5, sealed by the VM's own kernel firewall before the target's code runs, as a user without sudo; the `e2b` extra; the CI job `e2b` |
| Fix [C02] | [#120](https://github.com/DuhItzAniket/Repro/pull/120) | A run whose process is killed is ended as `failed` by the next process that opens the store, instead of staying "running" (a lease renewed every minute; store schema version 2) |
| Dependencies | [#121](https://github.com/DuhItzAniket/Repro/pull/121) | fast-uri 3.1.9, a development dependency, for four advisories |
| Presentation | [#122](https://github.com/DuhItzAniket/Repro/pull/122) | The README, both packages' PyPI pages (links, classifiers, keywords, a description written for PyPI), issue templates and a code of conduct |

## Compatibility
- The event schema gains a value, not a field: `e2b` as a sandbox backend. A log written by
  1.4.x reads as before. A reader older than 1.5.0 refuses a log that names `e2b`.
- The run store moves to schema version 2 the first time 1.5.0 opens it. Repro 1.4.x then
  refuses that store and says why ("newer than this Repro knows"); going back to 1.4.x needs
  another data directory.
- Nothing changes for a run without `sandbox = "e2b"`.

## Published
- GitHub release [v1.5.0](https://github.com/DuhItzAniket/Repro/releases/tag/v1.5.0), 2026-10-25. The release workflow
  ([run 40179090772](https://github.com/DuhItzAniket/Repro/actions/runs/40179090772)) built both wheels, installed them and made a
  first run, then attached them to the release.
- PyPI, through trusted publishing on 2026-10-25:
  [repro-events 1.5.0](https://pypi.org/project/repro-events/1.5.0/), then
  [repro-engine 1.5.0](https://pypi.org/project/repro-engine/1.5.0/).
- Zenodo: [10.5281/zenodo.23638272](https://doi.org/10.5281/zenodo.23638272), a version of
  the concept DOI [10.5281/zenodo.23524047](https://doi.org/10.5281/zenodo.23524047).
