# Release 1.6.2 — unchanged test cases, and how to choose the AI reviewers

| | |
|---|---|
| **Release** | `v1.6.2` |
| **Date** | 2026-11-01 |
| **Kind** | A patch release: one fix and documentation, no new feature, no breaking change |

## What it carries
| Part | PRs | What it brought |
|---|---|---|
| Fix | [#140](https://github.com/DuhItzAniket/Repro/pull/140) | Each parametrized case's own parameters get a digest in the pytest plugin; a case whose test changed only in its `parametrize` lists, with the same ID and digest on both sides, is kept, not `modified`. Found on Lectris PR #11, where 31 old cases were `modified` and a reviewer contradicted the true claim that no existing case changed |
| Docs | [#141](https://github.com/DuhItzAniket/Repro/pull/141) | The README, the GitHub Action's guide and the PyPI page: how to turn the AI reviewers on, the three backends, choosing a model, the budget |

## Compatibility
- No event schema or store change. Logs written by 1.6.1 read and replay as before.
- The coverage map's format is now 4, so cached maps are made again on the next run.
- A case whose parameters are not plain built-in values gets no digest and stays `modified`, as
  before.

## Published
- GitHub release [v1.6.2](https://github.com/DuhItzAniket/Repro/releases/tag/v1.6.2), 2026-11-01. The release workflow
  ([run 41312289152](https://github.com/DuhItzAniket/Repro/actions/runs/41312289152)) built both wheels, installed them and made a
  first run, then attached them to the release.
- PyPI, through trusted publishing on 2026-11-01:
  [repro-events 1.6.2](https://pypi.org/project/repro-events/1.6.2/), then
  [repro-engine 1.6.2](https://pypi.org/project/repro-engine/1.6.2/).
- Zenodo: [10.5281/zenodo.23858938](https://doi.org/10.5281/zenodo.23858938), a version of
  the concept DOI [10.5281/zenodo.23524047](https://doi.org/10.5281/zenodo.23524047).
