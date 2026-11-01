"""Base→head outcome classification (D06, ARCHITECTURE §4.2).

A test **passes** when pytest does not count it as failing and it ran: ``passed``, ``xfailed``
(it failed as declared) or ``xpassed``. ``failed`` and ``error`` fail, and a ``skipped`` test
does not pass either: a test skipped on head verifies nothing there.

Each selected test is classified from three runs: A (base), A2 (base again, another hash seed and
the reverse order) and H (head):

- only on head: ``new``; only on base: ``removed``;
- A and A2 disagree: ``flaky`` (its outcome depends on order or hashing, not on the change);
- its own code changed: ``modified`` (base and head outcomes are of different tests). A case
  of a parametrized test whose own parameters are the same on both sides, the test's code
  differing only in its other cases, is not (``repro.runner.testdiff.kept_cases``);
- otherwise ``P2P``, ``F2F``, or a flip, ``F2P`` / ``P2F``. A flip is run again *k* times on
  each side; unless every rerun repeats it, it is ``flaky``.

A flip on a test whose head run executed none of the changed code is marked *likely flaky*
(DeFlaker's check) before reruns decide; the mark stays on the evidence either way.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from repro import __version__
from repro.ids import new_evidence_id

OutcomeClass = Literal["F2P", "P2P", "P2F", "F2F", "new", "removed", "modified", "flaky"]
PASSING: Final = frozenset({"passed", "xfailed", "xpassed"})
#: Reruns of a flip on each side (research/03 §10: k = 3).
RERUNS: Final = 3
_ORIGIN: Final[dict[str, str]] = {
    "F2P": "introduced",
    "P2F": "introduced",
    "new": "introduced",
    "removed": "introduced",
    "modified": "introduced",
    "F2F": "pre-existing",
    "P2P": "n/a",
    "flaky": "n/a",
}


def passes(outcome: str | None) -> bool:
    return outcome in PASSING


@dataclass(frozen=True, slots=True)
class OutcomeDiff:
    test: str
    base: str | None  # A's outcome; None if the test is not on base
    base_again: str | None  # A2's outcome
    head: str | None  # H's outcome; None if the test is not on head
    outcome_class: OutcomeClass
    base_reruns: tuple[str | None, ...] = ()
    head_reruns: tuple[str | None, ...] = ()
    likely_flaky: bool = False
    base_message: str | None = None
    head_message: str | None = None

    @property
    def stable(self) -> bool:
        return self.outcome_class != "flaky"

    @property
    def flip(self) -> bool:
        return self.outcome_class in ("F2P", "P2F")

    @property
    def status(self) -> str:
        """The ``diff.outcome`` envelope status (ADR-0015 §2): ``ok`` for P2P, F2P and a new
        test passing on head; ``fail`` for P2F and a new test failing on head; ``warn`` for
        F2F, removed, modified and flaky."""
        if self.outcome_class in ("P2P", "F2P"):
            return "ok"
        if self.outcome_class == "new":
            return "ok" if passes(self.head) else "fail"
        return "fail" if self.outcome_class == "P2F" else "warn"

    def summary(self) -> str:
        base = self.base or "absent"
        head = self.head or "absent"
        text = f"{self.outcome_class}: {self.test} (base {base}, head {head})"
        if self.outcome_class == "flaky":
            text += f"; base again {self.base_again or 'absent'}"
        if self.likely_flaky:
            text += "; it ran none of the changed code"
        return text[:280]

    def evidence(self, run_id: str) -> dict[str, Any]:
        """The ``test_outcome`` evidence record (``evidence.created``; the writer adds ``seq``)."""
        return {
            "id": new_evidence_id(),
            "run_id": run_id,
            "type": "test_outcome",
            "targets": [{"test": self.test}],
            "summary": self.summary(),
            "data": {
                "class": self.outcome_class,
                "base_outcome": self.base,
                "base_again_outcome": self.base_again,
                "head_outcome": self.head,
                "base_reruns": list(self.base_reruns),
                "head_reruns": list(self.head_reruns),
                "likely_flaky": self.likely_flaky,
                "base_message": self.base_message,
                "head_message": self.head_message,
            },
            "artifacts": [],
            "stability": "stable" if self.stable else "unstable",
            "origin": _ORIGIN[self.outcome_class],
            "produced_by": {"component": "repro.runner.outcomes", "version": __version__},
        }

    def payload(self, evidence_id: str) -> dict[str, Any]:
        """The ``diff.outcome`` payload (its envelope's target is ``{"test": test}``)."""
        return {
            "evidence_id": evidence_id,
            "base_outcome": self.base,
            "head_outcome": self.head,
            "class": self.outcome_class,
            "reruns": {"base": len(self.base_reruns), "head": len(self.head_reruns)},
        }


def is_flip(base: str | None, base_again: str | None, head: str | None, *, modified: bool) -> bool:
    """Whether a test flipped between A and H, A2 agreeing with A: the tests to run again."""
    if base is None or head is None or modified:
        return False
    if base_again is not None and passes(base) != passes(base_again):
        return False
    return passes(base) != passes(head)


def classify(
    test: str,
    *,
    base: str | None,
    base_again: str | None,
    head: str | None,
    modified: bool,
    base_reruns: Sequence[str | None] = (),
    head_reruns: Sequence[str | None] = (),
    ran_changed_code: bool = True,
    base_message: str | None = None,
    head_message: str | None = None,
) -> OutcomeDiff:
    """One test's class from its runs (see the module's docstring)."""

    def result(kind: OutcomeClass, *, likely_flaky: bool = False) -> OutcomeDiff:
        return OutcomeDiff(
            test,
            base,
            base_again,
            head,
            kind,
            tuple(base_reruns),
            tuple(head_reruns),
            likely_flaky,
            base_message,
            head_message,
        )

    if base is None or head is None:
        return result("new" if base is None else "removed")
    if base_again is not None and passes(base) != passes(base_again):
        return result("flaky")
    if modified:
        return result("modified")
    if passes(base) == passes(head):
        return result("P2P" if passes(base) else "F2F")
    repeated = (
        len(base_reruns) >= RERUNS
        and len(head_reruns) >= RERUNS
        and all(passes(r) == passes(base) for r in base_reruns)
        and all(passes(r) == passes(head) for r in head_reruns)
    )
    kind: OutcomeClass = ("F2P" if passes(head) else "P2F") if repeated else "flaky"
    return result(kind, likely_flaky=not ran_changed_code)


def unstable_observation(diff: OutcomeDiff) -> dict[str, Any]:
    """The ``missing.recorded`` payload for a flaky test: its outcome is not evidence."""
    runs = f"base {diff.base}, base again {diff.base_again}, head {diff.head}"
    if diff.base_reruns or diff.head_reruns:
        runs += f"; reruns base {list(diff.base_reruns)}, head {list(diff.head_reruns)}"
    return {
        "missing": {
            "reason": "unstable_observation",
            "targets": [{"test": diff.test}],
            "detail": f"{diff.test} does not give the same outcome every run ({runs})"[:1000],
            "resolution": "Make the test independent of test order, hashing and timing",
        }
    }
