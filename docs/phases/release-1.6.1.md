# Release 1.6.1 — covered classes, and the evidence a reviewer sees first

| | |
|---|---|
| **Release** | `v1.6.1` |
| **Date** | 2026-10-31 |
| **Kind** | A patch release: two fixes, no new feature, no breaking change |

## What it carries
| Part | PRs | What it brought |
|---|---|---|
| Fix [D05] | [#133](https://github.com/DuhItzAniket/Repro/pull/133) | Methods that dataclasses, attrs and `NamedTuple` generate are mapped to their class, so constructing a class covers it; test selection reaches a package's `__init__.py` from its modules' importers; a class whose body is all its code is treated as module-level code. Found on Lectris, where seven small dataclasses and a docstring-only `__init__.py` were `no_covering_test` items |
| Fix [G03] | [#137](https://github.com/DuhItzAniket/Repro/pull/137) | `list_evidence` and the grouped reviewer prompt order evidence by how decisive it is, and collapse alike items whose outcome did not change. Found on Lectris PR #11, whose new test cases and probe never reached the reviewers |

## Compatibility
- The coverage map's format is now 3; cached maps are made again on the next run.
- No event schema or store change. Logs written by 1.6.0 read and replay as before.
- A reviewer's tool answers can differ in order from 1.6.0 for the same evidence; no committed
  cassette records one.

## Published
Filled in after the tag: the GitHub release, PyPI and Zenodo.
