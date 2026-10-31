"""A run's evidence as agents see it: listed, related to targets, rendered (G03).

Agents may cite only evidence the engine produced (ground rule 1), so what they read of it is
built here from the run's own log: ``evidence.created`` payloads, ``symbol.changed`` for which
file each changed symbol is in, and ``test.selected`` for the changed symbols each test was
selected for, so that a test is evidence about the code it exercises (G06).

**Rendering is the text quotes are checked against** (G05: every quote must be an exact
substring of it), so it is a pure function of the evidence and the same on every machine and in
every run of the same change:

- items are ordered by their content (type, targets, summary), not by ID: IDs follow creation
  time, which varies with how a run's jobs happened to interleave;
- only fields that describe the finding are shown, per type; what varies between runs of the
  same change (timings, process IDs, digests, artifacts) is left out, and a performance ratio
  is shown to one decimal;
- the engine's bookkeeping (``environment``, ``policy``) is not shown;
- strings are made portable (``repro.agents.untrusted.portable``) and cut at ``MAX_FIELD``.

Everything that can carry the target's own text (summaries, messages, values, details) is
inside an ``untrusted_content`` block; the evidence ID, type, stability, origin and targets,
which are the engine's, are outside it.

**A listing shows the most decisive evidence first** (``rank``): it is cut at a few thousand
characters, and a reviewer judges by what it saw. A change that adds cases to a parametrized
test makes every old case of it ``modified`` (its code changed), with the same outcome on both
sides; on a Lectris change, 31 such cases, ordered by target, filled the first listing and hid
the new cases and the probe that showed the fix, and two reviewers answered "unverifiable".
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from repro.agents.untrusted import block, portable
from repro.runner.outcomes import passes

#: Evidence agents are never shown: the engine's own bookkeeping.
HIDDEN: Final = frozenset({"environment", "policy"})
#: The fields shown per type, in this order; nested fields as ``a.b``.
FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "test_outcome": (
        "class",
        "base_outcome",
        "head_outcome",
        "base_again_outcome",
        "likely_flaky",
        "base_message",
        "head_message",
    ),
    "test_change": ("change", "detail"),
    "behaviour_diff": (
        "change",
        "calls",
        "test_modified",
        "reproducer.call",
        "reproducer.base",
        "reproducer.head",
        "reproducer.reproduced",
    ),
    "probe_diff": (
        "provenance",
        "change",
        "inputs_differing",
        "inputs_tried",
        "found_by",
        "reproducer.call",
        "reproducer.base",
        "reproducer.head",
    ),
    "api_diff": ("breakage", "old", "new"),
    "dep_diff": ("package", "change", "base", "head", "source"),
    "lock_drift": ("side", "package", "locked", "installed"),
    "perf_diff": ("verdict", "ratio"),
    "invariant": ("invariant", "result", "cites"),
    "policy_change": ("change", "weakened"),
    "network_attempt": ("side", "event", "detail", "count"),
    "subprocess_attempt": ("side", "event", "detail", "count"),
    "file_attempt": ("side", "event", "detail", "count"),
    "selection": (),
    "static_change": (),
}
#: Evidence types in the order a listing shows them.
TYPE_ORDER: Final = tuple(FIELDS)
MAX_FIELD: Final = 600
MAX_SUMMARY: Final = 400
_LIST_SUMMARY: Final = 160


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 15]} ...[{len(text)} chars]"


def _field(data: Mapping[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _show(value: Any) -> str:
    if isinstance(value, str):
        return _cut(portable(value), MAX_FIELD)
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return _cut(portable(json.dumps(value, sort_keys=True, ensure_ascii=False)), MAX_FIELD)


def target_text(target: Mapping[str, str]) -> str:
    return ", ".join(f"{key} {target[key]}" for key in ("file", "symbol", "test") if key in target)


def unchanged(item: "Item") -> bool:
    """An item that only says nothing changed: a test that passed on base and on head, or a
    timing that did not change."""
    if item.type == "test_outcome":
        return item.data.get("class") == "P2P"
    return item.type == "perf_diff" and item.data.get("verdict") == "no_change"


#: An item's place in a listing, the most decisive first (``rank``).
FAILING: Final = 0  # a problem on its own: a test failing on head, a failed invariant, ...
BEHAVIOUR: Final = 1  # what the code does or offers: behaviour, probe and API differences
OUTCOME: Final = 2  # a test outcome that differs: fixed, new, removed or flaky
OTHER: Final = 3  # any other difference: an edited test, a dependency, a pre-existing failure
CODE_ONLY: Final = 4  # a test whose code changed and whose outcome did not
UNCHANGED: Final = 5  # nothing changed (``unchanged``)
#: The ``test_change`` kind that is not a problem on its own (``diff.tests`` status ``warn``).
_EDITED: Final = "test_modified"


def _passed_throughout(data: Mapping[str, Any]) -> bool:
    """Whether a test passed on base, on base again (if it ran) and on head."""
    ran = [data.get(run) for run in ("base_outcome", "base_again_outcome", "head_outcome")]
    return (
        ran[0] is not None
        and ran[2] is not None
        and all(passes(outcome) for outcome in ran if outcome is not None)
    )


def rank(item: "Item") -> int:
    """How decisive ``item`` is, as its place in a listing: ``FAILING`` first, ``UNCHANGED``
    last. A ``modified`` test (its code changed, so the engine does not compare its outcomes)
    that passed on every run is ``CODE_ONLY``: an old case of a parametrized test that gained
    cases says little about the change."""
    data = item.data
    if unchanged(item):
        return UNCHANGED
    if item.type == "test_outcome":
        if item.origin == "pre-existing":
            return OTHER
        head = data.get("head_outcome")
        if head is not None and not passes(head):
            return FAILING
        modified = data.get("class") == "modified"
        return CODE_ONLY if modified and _passed_throughout(data) else OUTCOME
    if item.type in ("behaviour_diff", "probe_diff", "api_diff"):
        return BEHAVIOUR
    flagged = (
        (item.type == "test_change" and data.get("change") != _EDITED)
        or (item.type == "invariant" and data.get("result") == "fail")
        or (item.type == "perf_diff" and data.get("verdict") == "regression")
        or item.type == "policy_change"
    )
    return FAILING if flagged else OTHER


def most_decisive_first(items: Iterable["Item"]) -> list["Item"]:
    """``items`` by ``rank``, each rank in the order given. On a real repository the tests that
    passed on both sides are most of the evidence (272 of 304 items on an itsdangerous change),
    and they come last."""
    return sorted(items, key=rank)


def unparametrized(test: str) -> str:
    """A test's ID without its parameters: the test a parametrized case (pytest's
    ``path::name[id]``) is a case of. Another ID, such as a JavaScript test named with
    brackets, is left as it is."""
    start = test.find("::")
    bracket = test.find("[", start + 2) if start >= 0 else -1
    if bracket < 0 or not test.endswith("]"):
        return test
    name = test[:bracket].rpartition("::")[2]
    return test[:bracket] if name.isidentifier() else test


def group_key(item: "Item") -> tuple[str, str, str]:
    """Items alike enough to show as one: the same type, kind of change and first target, the
    cases of a parametrized test being one target."""
    data = item.data
    change = str(data.get("change") or data.get("class") or data.get("verdict") or "")
    target = item.targets[0] if item.targets else {}
    test = unparametrized(target["test"]) if "test" in target else ""
    return item.type, change, target.get("symbol") or test or target.get("file", "")


@dataclass(frozen=True, slots=True)
class Item:
    """One evidence record an agent may see and cite."""

    id: str
    type: str
    targets: tuple[Mapping[str, str], ...]
    summary: str
    data: Mapping[str, Any]
    stability: str
    origin: str

    @property
    def order(self) -> tuple[int, str, str, str]:
        rank = TYPE_ORDER.index(self.type) if self.type in TYPE_ORDER else len(TYPE_ORDER)
        targets = json.dumps(list(self.targets), sort_keys=True)
        return (rank, targets, self.shown_summary, self.id)

    @property
    def shown_summary(self) -> str:
        if self.type == "perf_diff":  # the summary's numbers vary run to run
            test = next((t["test"] for t in self.targets if "test" in t), "the test")
            return f"{self.data.get('verdict', 'unknown')}: {test}"
        return _cut(portable(self.summary), MAX_SUMMARY)

    def line(self) -> str:
        """One line for a listing: ID, type, stability, origin, summary."""
        return (
            f"{self.id}  {self.type}  {self.stability}  {self.origin}  "
            f"{_cut(self.shown_summary, _LIST_SUMMARY)}"
        )

    def render(self) -> str:
        """The full view ``get_evidence`` returns, and quotes are checked against."""
        targets = "; ".join(target_text(t) for t in self.targets)
        header = (
            f"evidence {self.id}\ntype: {self.type}\nstability: {self.stability}\n"
            f"origin: {self.origin}\ntargets: {targets}"
        )
        lines = [f"summary: {self.shown_summary}"]
        for name in FIELDS.get(self.type, tuple(sorted(self.data))):
            value = _field(self.data, name)
            if value is not None:
                lines.append(f"{name}: {_show(value)}")
        return f"{header}\n{block('evidence', chr(10).join(lines), id=self.id)}"


class EvidenceView:
    """A run's citable evidence, the files its changed symbols are in, and the changed symbols
    each test was selected for."""

    def __init__(
        self,
        items: Iterable[Item],
        symbol_files: Mapping[str, str],
        test_symbols: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.items = sorted(items, key=lambda item: item.order)
        self.by_id = {item.id: item for item in self.items}
        self.symbol_files = dict(symbol_files)
        self.test_symbols = {test: tuple(found) for test, found in (test_symbols or {}).items()}

    @classmethod
    def from_events(cls, events: Iterable[Mapping[str, Any]]) -> "EvidenceView":
        items, files, tests = [], {}, {}
        for event in events:
            payload = event["payload"]
            if event["kind"] == "symbol.changed":
                files[payload["symbol_id"]] = payload["file"]
            elif event["kind"] == "test.selected":
                test = (event.get("target") or {}).get("test")
                if test:
                    tests[test] = list(payload.get("symbols") or [])
            elif event["kind"] == "evidence.created":
                record = payload["evidence"]
                if record["type"] in HIDDEN:
                    continue
                items.append(
                    Item(
                        id=record["id"],
                        type=record["type"],
                        targets=tuple(dict(t) for t in record["targets"]),
                        summary=record["summary"],
                        data=record["data"],
                        stability=record["stability"],
                        origin=record["origin"],
                    )
                )
        return cls(items, files, tests)

    def _files_of(self, target: Mapping[str, str]) -> set[str]:
        """The files a target is about: its own, its symbol's, its test's, and the files of the
        changed symbols its test was selected for (G06: a test is evidence about the code it
        exercises, not only about its own file)."""
        files = {target["file"]} if "file" in target else set()
        if "symbol" in target and target["symbol"] in self.symbol_files:
            files.add(self.symbol_files[target["symbol"]])
        if "test" in target:
            files.add(target["test"].split("::", 1)[0])
            for symbol in self.test_symbols.get(target["test"], ()):
                if symbol in self.symbol_files:
                    files.add(self.symbol_files[symbol])
        return files

    def related(self, item: Item, target: Mapping[str, str]) -> bool:
        """Whether ``item`` is about ``target``: the same test or symbol, or a file in common (a
        symbol's file being where it changed; a test's, the file it is in and the files of the
        changed symbols it was selected for)."""
        wanted = self._files_of(target)
        for own in item.targets:
            if any(own.get(key) and own.get(key) == target.get(key) for key in ("symbol", "test")):
                return True
            if wanted & self._files_of(own):
                return True
        return False

    @staticmethod
    def about(item: Item, target: Mapping[str, str]) -> bool:
        """Whether ``item`` is about ``target`` itself, not through a file in common: the same
        symbol, the same test or a case of it, or a target in the same file."""
        for own in item.targets:
            files = (own.get("file"), own.get("test", "").split("::", 1)[0])
            if (
                ("symbol" in target and own.get("symbol") == target["symbol"])
                or (
                    "test" in target
                    and "test" in own
                    and unparametrized(own["test"]) == unparametrized(target["test"])
                )
                or ("file" in target and target["file"] in files)
            ):
                return True
        return False

    def relevant(self, targets: Sequence[Mapping[str, str]]) -> list[Item]:
        """The items about any of ``targets``, in rendering order; all of them if none given."""
        if not targets:
            return list(self.items)
        return [item for item in self.items if any(self.related(item, t) for t in targets)]
