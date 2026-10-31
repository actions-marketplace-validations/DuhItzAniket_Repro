"""The read-only tools agents call: evidence, source and diffs of the change (G03).

Four tools (research/04, recommendation A.2), each a ``ReproTool`` that reads only the run's
evidence (``repro.agents.evidence``) and the two commits (``repro.agents.sources``):

- ``list_evidence``: the citable evidence, one line each, the most decisive first
  (``repro.agents.evidence.rank``), optionally only what is about a file, symbol or test, what
  is about that target itself first; alike items whose outcome is the same on both sides (the
  old cases of a parametrized test whose code changed, tests that passed on both sides) take
  one line from ``COLLAPSE_AT`` on, which says how many more there are and how to list them;
- ``get_evidence``: one item in full; the text a quote must come from;
- ``read_snippet``: up to ``MAX_SNIPPET_LINES`` lines of a file on one side;
- ``get_diff``: a file's diff from base to head, up to ``MAX_DIFF_LINES`` lines.

Every input is checked here, whatever the schema promised, and refused with a message the
model can act on. Every output is capped, and everything from the target is in an
``untrusted_content`` block. The tools hold no state: the same input gives the same output, so
a cassette's replay of a call (G02) sees exactly what was recorded, and no text a tool reads
(a comment saying "ignore your instructions", a fake closing tag) changes what any tool does.
"""

import re
from collections.abc import Mapping
from typing import Any, Final

from repro.agents.backend import ReproTool
from repro.agents.evidence import (
    CODE_ONLY,
    EvidenceView,
    Item,
    group_key,
    most_decisive_first,
    rank,
)
from repro.agents.sources import RepoSources, SourceError, checked_path
from repro.agents.untrusted import block

MAX_LISTED: Final = 60
#: Alike items (``group_key``) whose outcome is the same on both sides, from this many on, are
#: listed as one line. On a Lectris change, 31 old cases of two parametrized tests, each line
#: alike, took most of a listing's ``MAX_ANSWER_CHARS``.
COLLAPSE_AT: Final = 3
MAX_SNIPPET_LINES: Final = 80
MAX_LINE: Final = 240
MAX_DIFF_LINES: Final = 160
MAX_ARGUMENT: Final = 1024
#: The most characters of lines one tool answer holds, cut at a whole line. An agent's
#: conversation is sent again each turn; on a free tier that counts the answer's allowance too
#: (8,000 tokens a minute, 3,000 of them reserved for the answer), a listing of 60 items in a
#: reviewer's second turn was enough to refuse the call (8,819 tokens, on an itsdangerous
#: change).
MAX_ANSWER_CHARS: Final = 4000
_EVIDENCE_ID: Final = re.compile(r"ev_[0-9A-HJKMNP-TV-Z]{26}")
_TARGET_KEYS: Final = ("file", "symbol", "test")
_SNIPPET_KEYS: Final = ("side", "path", "start", "end")

TARGET_SCHEMA: Final = {
    "type": "object",
    "properties": {
        "file": {"type": "string", "description": "a repository path, '/'-separated"},
        "symbol": {"type": "string", "description": "a symbol ID, module:qualname#n"},
        "test": {"type": "string", "description": "a test ID, path::name"},
    },
    "additionalProperties": False,
}
EVIDENCE_SCHEMA: Final = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "the evidence ID, ev_ and 26 characters"}
    },
    "required": ["id"],
    "additionalProperties": False,
}
SNIPPET_SCHEMA: Final = {
    "type": "object",
    "properties": {
        "side": {"type": "string", "enum": ["base", "head"]},
        "path": {"type": "string", "description": "a repository path, '/'-separated"},
        "start": {"type": "integer", "minimum": 1},
        "end": {"type": "integer", "minimum": 1},
    },
    "required": list(_SNIPPET_KEYS),
    "additionalProperties": False,
}
DIFF_SCHEMA: Final = {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "a repository path, '/'-separated"}},
    "required": ["path"],
    "additionalProperties": False,
}


class ToolInputError(ValueError):
    """An input a tool refuses, said so the model can correct it."""


def _only(arguments: Mapping[str, Any], allowed: tuple[str, ...]) -> None:
    extra = sorted(set(arguments) - set(allowed))
    if extra:
        raise ToolInputError(f"unknown arguments {extra}; this tool takes {list(allowed)}")


def _strings(arguments: Mapping[str, Any], allowed: tuple[str, ...]) -> dict[str, str]:
    _only(arguments, allowed)
    found = {}
    for name in allowed:
        value = arguments.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > MAX_ARGUMENT:
            raise ToolInputError(f"{name} is a non-empty string of at most {MAX_ARGUMENT} chars")
        found[name] = value
    return found


def _line_number(arguments: Mapping[str, Any], name: str) -> int:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolInputError(f"{name} is a line number, 1 or more")
    return value


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE else f"{line[: MAX_LINE - 12]} ...[cut]"


def _fit(lines: list[str]) -> list[str]:
    """The first of ``lines`` that fit ``MAX_ANSWER_CHARS`` together, at least one."""
    kept: list[str] = []
    used = 0
    for line in lines:
        used += len(line) + 1
        if kept and used > MAX_ANSWER_CHARS:
            break
        kept.append(line)
    return kept


def _listed(
    view: EvidenceView, items: list[Item], target: Mapping[str, str]
) -> list[tuple[str, int]]:
    """A listing's lines, each with how many items it stands for. Alike items (``rank`` and
    ``group_key``) that say no outcome changed, ``COLLAPSE_AT`` or more of them, are one line
    where the first of them is, unless they are about the target itself: listing their test
    shows each."""

    def collapsible(item: Item) -> bool:
        return rank(item) >= CODE_ONLY and not (target and view.about(item, target))

    groups: dict[tuple[int, tuple[str, str, str]], list[Item]] = {}
    for item in items:
        if collapsible(item):
            groups.setdefault((rank(item), group_key(item)), []).append(item)
    lines = []
    for item in items:
        group = groups.get((rank(item), group_key(item)), []) if collapsible(item) else []
        if len(group) < COLLAPSE_AT:
            lines.append((item.line(), 1))
        elif item is group[0]:
            first = item.targets[0] if item.targets else {}
            by = next((key for key in ("symbol", "test", "file") if key in first), "file")
            alike = (
                "the same outcome on base and head, the test's code changed"
                if rank(item) == CODE_ONLY
                else "nothing changed"
            )
            more = (
                f"  [and {len(group) - 1} more like it ({alike}): list_evidence with {by} "
                f"{group_key(item)[2]} lists each]"
            )
            lines.append((item.line() + more, len(group)))
    return lines


class EvidenceTools:
    """The four tools over one run's evidence and commits."""

    def __init__(self, view: EvidenceView, sources: RepoSources) -> None:
        self.view = view
        self.sources = sources

    async def list_evidence(self, arguments: Mapping[str, Any]) -> str:
        target = _strings(arguments, _TARGET_KEYS)
        if "file" in target:
            try:
                checked_path(target["file"])
            except SourceError as exc:
                raise ToolInputError(str(exc)) from None
        items = most_decisive_first(self.view.relevant([target] if target else []))
        about = f" about {', '.join(f'{k} {v}' for k, v in target.items())}" if target else ""
        if not items:
            return f"no evidence{about}"
        if target:  # what is about the target itself first, each part most decisive first
            items.sort(key=lambda item: not self.view.about(item, target))
        lines = _listed(self.view, items, target)[:MAX_LISTED]
        shown = _fit([line for line, _ in lines])
        left = len(items) - sum(count for _, count in lines[: len(shown)])
        more = (
            f"\n...and {left} more; a file, symbol or test lists only what is about it, what "
            "is about it itself first"
            if left
            else ""
        )
        order = (
            "what is about it itself first, each part the most decisive first"
            if target
            else "the most decisive first"
        )
        head = (
            f"{len(items)} evidence items{about} ({order}; id, type, stability, origin, summary):"
        )
        listing = "\n".join(shown) + more
        return f"{head}\n{block('evidence-list', listing)}"

    async def get_evidence(self, arguments: Mapping[str, Any]) -> str:
        ident = _strings(arguments, ("id",)).get("id", "")
        if not _EVIDENCE_ID.fullmatch(ident):
            raise ToolInputError("id is an evidence ID: ev_ and 26 characters")
        item = self.view.by_id.get(ident)
        if item is None:
            raise ToolInputError(f"no evidence {ident} in this run; list_evidence lists them")
        return item.render()

    async def read_snippet(self, arguments: Mapping[str, Any]) -> str:
        _only(arguments, _SNIPPET_KEYS)
        side, path = arguments.get("side"), arguments.get("path")
        start, end = _line_number(arguments, "start"), _line_number(arguments, "end")
        if end < start or end - start + 1 > MAX_SNIPPET_LINES:
            raise ToolInputError(f"end is at least start, and at most {MAX_SNIPPET_LINES} lines on")
        try:
            text = self.sources.read(side, path)
        except SourceError as exc:
            raise ToolInputError(str(exc)) from None
        if text is None:
            return f"{side} has no file {path}"
        lines = text.split("\n")
        if text.endswith("\n"):
            lines.pop()
        if start > len(lines):
            return f"{side}:{path} has {len(lines)} lines"
        asked = range(start, min(end, len(lines)) + 1)
        width = len(str(asked[-1]))
        kept = _fit([f"{n:>{width}}| {_clip(lines[n - 1])}" for n in asked])
        last = start + len(kept) - 1
        body = "\n".join(kept)
        head = f"{side}:{path} lines {start}-{last} of {len(lines)}" + (
            f" (cut at {MAX_ANSWER_CHARS} characters; ask from line {last + 1} for the rest)"
            if last < asked[-1]
            else ""
        )
        return f"{head}\n{block('source', body, side=str(side), path=str(path))}"

    async def get_diff(self, arguments: Mapping[str, Any]) -> str:
        path = _strings(arguments, ("path",)).get("path")
        try:
            diff = self.sources.diff(path)
        except SourceError as exc:
            raise ToolInputError(str(exc)) from None
        if not diff:
            return f"{path} is the same on base and head"
        lines = diff.rstrip("\n").split("\n")
        shown = _fit([_clip(line) for line in lines[:MAX_DIFF_LINES]])
        if len(lines) > len(shown):
            shown.append(f"...{len(lines) - len(shown)} more lines; read_snippet shows any part")
        body = "\n".join(shown)
        return f"diff of {path}, base to head\n{block('diff', body, path=str(path))}"

    def tools(self) -> tuple[ReproTool, ...]:
        return (
            ReproTool(
                "list_evidence",
                "Lists the run's citable evidence, one line each; give a file, symbol or test "
                "to list only what is about it.",
                TARGET_SCHEMA,
                self.list_evidence,
            ),
            ReproTool(
                "get_evidence",
                "Shows one evidence item in full. Quotes in a verdict must be copied from it.",
                EVIDENCE_SCHEMA,
                self.get_evidence,
            ),
            ReproTool(
                "read_snippet",
                f"Shows lines of a file on base or head, at most {MAX_SNIPPET_LINES} lines.",
                SNIPPET_SCHEMA,
                self.read_snippet,
            ),
            ReproTool(
                "get_diff", "Shows a file's diff from base to head.", DIFF_SCHEMA, self.get_diff
            ),
        )
