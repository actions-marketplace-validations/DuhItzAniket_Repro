# Release 1.6.0 — the selected tests on the stage, and Claude reviewers that always answer

| | |
|---|---|
| **Release** | `v1.6.0` |
| **Date** | 2026-10-31 |
| **Kind** | A minor release: one new feature, two fixes, a CI change, no breaking change |

## What it carries
| Part | PRs | What it brought |
|---|---|---|
| B14 | [#131](https://github.com/DuhItzAniket/Repro/pull/131) | A graph node per selected test and a `covers` edge to each changed symbol that selected it, emitted before `test.selected`, for Python and JavaScript/TypeScript (at most 600 test nodes); the stage's bunches of strands fire from them. Measured on one real run: 228 test nodes, and every `trace.sample` bunch went from 0 strands to 8 |
| Fix [G01] | [#132](https://github.com/DuhItzAniket/Repro/pull/132) | A Claude call with an output schema that ends `error_max_turns` resumes its session for up to two turns, offered only `StructuredOutput`, within the budget and time left. Measured live on one pull request: 6 of 8 claims judged before, 8 of 8 after |
| Fix [D03] | [#126](https://github.com/DuhItzAniket/Repro/pull/126) | A sandbox image that does not build names its cause from the failing step's output |
| CI | [#124](https://github.com/DuhItzAniket/Repro/pull/124) | A pull request that changes only documentation skips the slow jobs; the required checks still report |
| Docs | [#125](https://github.com/DuhItzAniket/Repro/pull/125) | Where releases 1.4.0 and 1.5.0 were published |

## Compatibility
- The event schema allows `graph.node` in the `run` stage as well as `build`, as it already
  allowed `graph.edge`. No field changes and `pv` stays 1. A log written by 1.5.x reads and
  replays as before. A reader older than 1.6.0 refuses a log with a `graph.node` in the run
  stage, which every 1.6.0 run that selects tests has.
- A Claude call's `turns` now counts the model's responses, not the CLI's `num_turns`.
- The card, the gap rules and the agents' evidence do not read the graph; a card is unchanged.

## Published
Filled in after the tag: the GitHub release, PyPI and Zenodo.
