# Reading a card

A Reproduction Card is what one run concluded about one change. It is assembled from the run's
event log alone, so `repro replay` and `repro export` can rebuild it and check it against the
card the run published. It starts with the repository and the change (base and head, each by
the ref given and its commit). For a pull request (`--pr`), base is the base branch's name
with the commit where the pull request left it, and head is the pull request's label
(`owner:branch`) with its last commit. Below, its parts in the order the Markdown card shows them
(`repro export <run> --format md`).

The cockpit's card drawer is a summary of the same card. Its gap and missing tables have fewer
columns (click a gap row for its evidence), and it adds counts, where *Unexplained* is the
number of gap items that flag. Only the Markdown card says what would resolve each missing
item.

## The status
One of three, decided by fixed rules; no language model decides it.

| Status | Means | When |
|---|---|---|
| **FLAGGED** | The run found a problem | A difference no claim explains, a contradicted claim, or a failed invariant |
| **UNVERIFIABLE** | Too much could not be checked to say | An environment not built, a test run that did not finish or a test that never reported, a job that did not finish, too many changed functions no test reaches, an invariant not evaluated, a claim not supported |
| **VERIFIED** | Every claim held, nothing changed that was not claimed, every invariant held | Neither of the above |

FLAGGED wins over UNVERIFIABLE: something observed to be wrong outweighs something not seen.
VERIFIED never hides an unchecked part: everything the run could not check is listed under
**Missing**, whatever the status. A VERIFIED card says "everything was checked" only when
Missing is empty; otherwise it says that what Missing lists does not make the change
unverifiable (tests too fast to time, say, or fewer unreached functions than `repro.yml`
allows).

**Without agents** (the default), Repro reads no claims, so nothing can explain a difference:
every difference between base and head flags. A deterministic run says *what changed*; turning
agents on adds *whether the author said so*.

## Why
The rules that decided the status, each with the evidence or targets behind it. The first ones
decide; any after them are what was also incomplete.

| Rule | Status | Says |
|---|---|---|
| `gap_flagged` | FLAGGED | Differences no claim explains, by check and kind |
| `claim_contradicted` | FLAGGED | A claim the evidence contradicts |
| `invariant_failed` | FLAGGED | A `repro.yml` invariant the change broke |
| `policy_invalid` | UNVERIFIABLE | Base's `repro.yml` is not valid, so no invariant was evaluated |
| `environment_failed` | UNVERIFIABLE | A side's environment could not be built |
| `not_analysed` | UNVERIFIABLE | The change's reach (its blast radius) was not computed |
| `job_incomplete` | UNVERIFIABLE | A stage's job failed, timed out or was skipped |
| `tests_not_run` | UNVERIFIABLE | Selected tests that no run reported, none selected for the changed code, or a test run that did not finish (whatever was selected) |
| `coverage` | UNVERIFIABLE | More changed functions unverified than `repro.yml` allows (25 % by default) |
| `invariant_not_evaluated` | UNVERIFIABLE | An invariant Repro could not evaluate |
| `no_claims` | UNVERIFIABLE | No claim found, and `repro.yml` requires one |
| `claim_not_supported` | UNVERIFIABLE | A claim the evidence neither supports nor contradicts |
| `verdicts_rejected` | UNVERIFIABLE | Every verdict was rejected for want of evidence |

## Claims
What the pull request says the change does (its title, description or commits), each with a
verdict when agents are on:
- **supported**: the cited evidence bears it out;
- **contradicted**: the cited evidence shows otherwise;
- **unverifiable**: the evidence neither bears it out nor contradicts it.

Every verdict cites evidence by ID and quotes it exactly. A verdict that cites nothing, cites
evidence that does not exist or is unrelated, or misquotes it, is rejected, and its claim stays
unsupported. Verdicts come from language models: they are judgements, shown next to the
evidence they rest on.

## The runs and the tests
Not a part of the card, but the names it uses. Repro runs base's selected tests twice, **A**
and **A2** (the cockpit writes A′), with different hash seeds, so that what differs between
them is noise, not the change. It runs head's once, **H**. When no map is cached, base's first
run covers every test, to learn which test runs which code, and head's tests are first listed
(**head tests**). A test whose outcome flips is run again (**rerun 1**, …), and the base version
of a test the change edits is run against head's code (**base tests on head**). Missing items
and the cockpit name runs this way.

A JavaScript or TypeScript run (K08) runs the whole suite on A and H, with the map and the
traces, then A2 in a shuffled order. A test that flips is run again three times on each side.

The tests that run are those the change can affect:
- the tests that ran the changed code on base. A class's own code (an attribute, a base class,
  a decorator) runs when its module is imported, so a change to it selects the tests that ran
  one of its methods. The methods a dataclass, attrs or `NamedTuple` writes for a class count:
  constructing one runs it;
- the tests in modules that import, directly or not, a module whose own code changed, and the
  tests below a changed `conftest.py`. Importing `pkg.text.squash` runs `pkg/text/__init__.py`
  first, so it counts as importing `pkg.text`. A class with nothing but a body (an enum's
  members, an exception class) counts as its module's own code;
- the tests the change adds or edits.

A changed function counts as reached when a selected test ran it, on base or on head (a
function the change adds exists only on head). One that no selected test reaches is a
`no_covering_test` item under Missing.

Functions are named `module:qualified.name#n`: `#0` is the first definition of that name in the
file, and a second one with the same name (a property's setter, say) is `#1`. `<module>` is a
file's own code, outside any function or class.

## The gap
Every difference the run found between base and head, one row each.

| Column | Means |
|---|---|
| Evidence | The evidence ID (`ev_…`): `repro export --format json` holds the record |
| Check | Which comparison found it (below) |
| Kind | What kind of difference |
| Summary | The difference, in words |
| Origin | `introduced` by the change, or `pre-existing` on base too |
| Stability | `stable`, or `unstable` when the same code gave different results on base's two runs; `n/a` where it does not apply (a change to a test's own code, say) |
| Result | What the rules made of it (below) |

**The checks.**
- `diff.outcome`, a test's outcome on base and head:
  - `P2P` passed on both; `F2P` failed on base and passes on head;
  - `P2F` passed on base and fails on head, a problem whatever the claims say;
  - `F2F` failed on both: pre-existing;
  - `new` and `removed` tests;
  - `modified`: the test's own code changed, so each side ran its own version. Whether base's
    version still passes against head's code is a separate check, `diff.tests`'
    `base_test_fails_on_head`, so a test can read "passed on both" here and fail there;
  - `flaky`: base's two runs or the reruns disagree.
- `diff.tests`: how the tests themselves changed:
  - a test removed;
  - a skip or an expected failure added;
  - an assertion removed or weakened;
  - `pytest.raises` widened;
  - `test_modified`: a test's own code changed;
  - `base_test_fails_on_head`: the base version of a modified test fails against head's code.
- `diff.behaviour`: a changed function, called with the same input by the same test, behaved
  differently. The kinds are `result_changed`, an exception added, removed or changed,
  `input_mutated` (it changed an argument it used to leave alone), `order_only`, and
  `calls_changed` (it was called with other arguments).
- `diff.probe`: the same, for inputs Repro generated for the changed functions
  (`found_by: agent` when an agent proposed them).
- `diff.api`: a breaking change to the public API, as griffe finds it:
  - a parameter moved, removed, made required, or of another kind (positional, keyword);
  - a parameter's default changed, or a new required parameter;
  - a return type changed;
  - an object removed or of another kind;
  - an attribute's type or value changed (`attribute_changed_value`);
  - a base class removed.
- `diff.deps`: a dependency added, removed, upgraded, downgraded or changed, in the lock file,
  in what the project declares, or in what was installed.
- `diff.perf`: a selected test's timing, with base and head interleaved: a `regression` or an
  `improvement` beyond the noise measured on this machine.
- `policy_change`: head edited `repro.yml`, which is read from base all the same.

**The result.**
- `flagged`: a problem; it makes the card FLAGGED.
- `explained`: a supported claim cites it, and no contradicted one does.
- `passed`: good on its own, such as a fixed test or a faster one.
- `pre-existing`: present on base too, never held against the change.
- `unassessed`: it could not be compared (an unstable, flaky or modified test).

## Invariants
The checks `repro.yml` asks for, each `pass`, `fail` or `not_evaluated`, with the evidence it
rests on. See [`repro.yml`](repro-yml.md).

## Missing
Everything the run could not check, never counted as a pass. Each item has a reason, the tests,
functions or files it concerns, a detail, and, where Repro knows one, what would resolve it.

| Reason | What could not be checked |
|---|---|
| `no_covering_test` | A changed function no selected test reaches |
| `environment_failed` | A side whose environment could not be built or read, or whose sandbox could not start |
| `run_incomplete` | A test run that ended early or ran past its limit: the tests it had not reported have no outcome. Its detail ends with the line of its output that names the error |
| `collection_error` | A test module that could not be imported |
| `timeout` | A job, an analysis or a probe run that reached its time limit; what it had not done is missing |
| `memory_limit` | A run that reached its memory limit |
| `unstable_observation` | Calls whose results differed between base's two runs: noise, not compared |
| `trace_unavailable` | A run whose calls or test map could not be recorded |
| `trace_truncated` | Calls past the recording budget of a test and function |
| `subprocess_untraced` | Code run in a subprocess, which the tracer does not follow |
| `encoding_failed` | Arguments or results that could not be encoded completely, so not compared |
| `network_not_enforced` | The network was detected, not blocked (no container): `no_network`'s pass says so |
| `perf_not_assessable` | Tests too fast to time (under 5 ms on both sides), or a timing run lost |
| `unsupported_project` | Code Repro could not analyse: a file's functions, a package's public API or its imports could not be read |
| `claim_unverifiable` | A claim no evidence is about, so nothing could support it; a test that exercises it would |
| `verdict_rejected` | A verdict rejected for its citations |
| `agent_failed` | An agent call that failed or ran out of budget |
| `judge_is_author` | Every model judging the claims is of the family that wrote the change, as its commits or PR text name it (judges favour their own) |

## Conditions
How the run was made: the Python the target's tests ran on (3.13), the platform they ran on
(with Repro's sandbox image, by its digest, when in a container), the **isolation level**
reached (`L2` a contained process, `L3` a container with no network, `L4` a container under
gVisor, `L5` a container in a microVM with a kernel of its own), whether the network was `enforced` or only `detected-only`, the lock files' digests,
the tests selected, the random seeds of base's two runs, Repro's version and the models used.
A card never claims more isolation than the run had.

The lock digests are the SHA-256 of the file each side's requirements came from, `-` when there
is none: `uv.lock`, `poetry.lock` or `pdm.lock`, else the first of the project's requirements
files (`requirements.txt`, then `requirements-dev.txt` and the like, then
`requirements/tests.txt`).

A JavaScript or TypeScript card names **Node.js** where a Python card names Python. Its lock
digests are its lockfile's (`package-lock.json`, `pnpm-lock.yaml` or `yarn.lock`). Its **order
of A2** is the seed that shuffled base's second run: JavaScript has no hash seed to change. Its
isolation is `L3` with the Docker sandbox (`REPRO_SANDBOX=docker`), in containers on Repro's
Node.js image with no network, and `L2` otherwise (`auto` included). At L2 the card lists the
network as missing: a Node process's network use is neither blocked nor seen. At L3 it is
blocked, but still not seen: `no_network` passes on the block alone, and its message says so.

## In the cockpit
A check's evidence drawer shows its *Status*, the check's own verdict (`ok`, `warn`, `fail`)
before the status rules, and its *Result*, what the rules made of it (above). The change ×
check index marks each row `verified`, `flagged` or `review`. A claim's row is verified when
the claim is supported and none of its checks is still running; a change no claim describes
is never verified, only flagged or left for review.

## Replay
The event log the card rests on, by digest and number of events: the events before the card
was published. `repro run` counts the whole log, which goes on a few events after the card
(the run's end), so its number is larger. `repro replay <run>` shows the run again in the
cockpit, from the start; `repro export` assembles the card again from the log, and refuses one
that differs from what the run published. The cockpit's card also shows the card's own
SHA-256: the digest of its canonical JSON (sorted keys, no whitespace, UTF-8).
