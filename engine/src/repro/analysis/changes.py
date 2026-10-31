"""Which symbols a change touched, on both sides, and how (ARCHITECTURE §9).

The diff (C05) says which files changed. In each, base and head symbols are paired by qualified
name; a repeated name (overloads, a property's accessors) is aligned by own code, so a new
overload does not shift the implementation into a false change. Then each symbol is one change:

- ``modified``: paired, and its own code differs (a comment or a reformat is no change);
- ``renamed``: unpaired on both sides in the same module, with near-identical code (``SIMILAR``,
  names normalised);
- ``moved``: the same, but in another module; every symbol of a renamed file is moved;
- ``added`` and ``removed``: the rest.

A file whose module name changed with its content unchanged (an ``__init__.py`` added or
removed above it) is not in the diff, but its symbols moved too. A file that does not parse, or
has no module name, is reported, and its symbols are not guessed: calling them added or removed
would be a false claim.
"""

import difflib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from repro.analysis.modules import module_names
from repro.analysis.symbols import (
    MODULE_QUALNAME,
    Definition,
    Symbol,
    SymbolKind,
    SymbolTable,
    body_only,
    build_table,
)
from repro.gitops import Git, diff, read_blobs, regular_files
from repro.gitops.changes import FileChange

ChangeKind = Literal["added", "removed", "modified", "renamed", "moved"]
Side = Literal["base", "head"]
Files = Mapping[str, str]  # each regular file of a side: path -> blob sha
#: The least AST similarity for a rename or a move.
SIMILAR: Final = 0.8


@dataclass(frozen=True, slots=True)
class SymbolChange:
    symbol_id: str
    change: ChangeKind
    kind: SymbolKind
    file: str
    base_lines: tuple[int, int] | None
    head_lines: tuple[int, int] | None
    previous_symbol_id: str | None = None
    #: A class whose body is all its code on every side it is on (``symbols.body_only``): it
    #: runs at import only, so test selection treats it as module-level code.
    body_only: bool = False

    def payload(self) -> dict[str, Any]:
        """The ``symbol.changed`` payload."""
        return {
            "symbol_id": self.symbol_id,
            "change": self.change,
            "file": self.file,
            "base_lines": list(self.base_lines) if self.base_lines else None,
            "head_lines": list(self.head_lines) if self.head_lines else None,
            "previous_symbol_id": self.previous_symbol_id,
        }


@dataclass(frozen=True, slots=True)
class Unanalysed:
    """A Python file whose symbols could not be read."""

    path: str
    side: Side
    reason: str


@dataclass(frozen=True, slots=True)
class ChangedSymbols:
    changes: tuple[SymbolChange, ...]
    unanalysed: tuple[Unanalysed, ...]


@dataclass(frozen=True, slots=True)
class _Pairing:
    pairs: list[tuple[Symbol, Symbol, bool]]  # (base, head, same own code)
    gone: list[Symbol]  # only on base
    new: list[Symbol]  # only on head


def _span(symbol: Symbol | None) -> tuple[int, int] | None:
    return (symbol.start, symbol.end) if symbol is not None else None


def _change(
    change: ChangeKind, file: str, *, old: Symbol | None = None, new: Symbol | None = None
) -> SymbolChange:
    """One change; ``previous_symbol_id`` is set when the identity changed."""
    symbol = new if new is not None else old
    if symbol is None:
        raise ValueError("a change needs a symbol on at least one side")
    previous = old.id if old is not None and new is not None and old.id != new.id else None
    sides = [s for s in (old, new) if s is not None]
    return SymbolChange(
        symbol.id, change, symbol.kind, file, _span(old), _span(new), previous,
        body_only=all(body_only(s) for s in sides),
    )  # fmt: skip


def _has_own_code(symbol: Symbol) -> bool:
    """``<module>`` is a symbol only if it holds code: a file of definitions has none of its own."""
    if symbol.qualname != MODULE_QUALNAME or symbol.node is None:
        return True
    body: list[Any] = getattr(symbol.node, "body", [])
    return any(not isinstance(statement, Definition) for statement in body)


def _pair(base: SymbolTable, head: SymbolTable) -> _Pairing:
    """Base and head symbols paired by qualified name, repeated names aligned by own code."""
    keep = base.by_key().keys() & head.by_key().keys()
    base_code, head_code = base.own_code(keep), head.own_code(keep)
    before: dict[str, list[Symbol]] = {}
    after: dict[str, list[Symbol]] = {}
    for symbol in base.symbols:
        before.setdefault(symbol.qualname, []).append(symbol)
    for symbol in head.symbols:
        after.setdefault(symbol.qualname, []).append(symbol)
    result = _Pairing([], [], [])
    for qualname in dict.fromkeys([*before, *after]):
        olds, news = before.get(qualname, []), after.get(qualname, [])
        matcher = difflib.SequenceMatcher(
            None,
            [base_code[s.key] for s in olds],
            [head_code[s.key] for s in news],
            autojunk=False,
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            run_old, run_new = olds[i1:i2], news[j1:j2]
            if tag == "equal":
                result.pairs.extend(
                    (old, new, True) for old, new in zip(run_old, run_new, strict=True)
                )
                continue
            if len(run_old) == len(run_new) == 1:  # the common case: one definition, edited
                matched = [(0, 0)]
            else:  # a replaced run: the most alike own code pairs first
                matched = _greedy(
                    (_ratio(base_code[old.key], head_code[new.key]), i, j)
                    for i, old in enumerate(run_old)
                    for j, new in enumerate(run_new)
                )
            result.pairs.extend((run_old[i], run_new[j], False) for i, j in matched)
            paired_old = {i for i, _ in matched}
            paired_new = {j for _, j in matched}
            result.gone.extend(s for i, s in enumerate(run_old) if i not in paired_old)
            result.new.extend(s for j, s in enumerate(run_new) if j not in paired_new)
    return result


def _ratio(a: Sequence[str], b: Sequence[str]) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _greedy(scored: Iterable[tuple[float, int, int]]) -> list[tuple[int, int]]:
    """``(i, j)`` pairs, best score first, each index used once (ties: lowest indices)."""
    used_i: set[int] = set()
    used_j: set[int] = set()
    pairs = []
    for _score, i, j in sorted(scored, key=lambda c: (-c[0], c[1], c[2])):
        if i not in used_i and j not in used_j:
            used_i.add(i)
            used_j.add(j)
            pairs.append((i, j))
    return pairs


def _similarity(a: Symbol, b: Symbol) -> float:
    if (a.kind == "class") != (b.kind == "class"):
        return 0.0  # a class never becomes a function, nor the reverse
    matcher = difflib.SequenceMatcher(None, a.fingerprint, b.fingerprint, autojunk=False)
    if matcher.real_quick_ratio() < SIMILAR or matcher.quick_ratio() < SIMILAR:
        return 0.0
    return matcher.ratio()


class _Collector:
    def __init__(self, base_files: Files, head_files: Files) -> None:
        self.files: dict[Side, Files] = {"base": base_files, "head": head_files}
        base_names, head_names = module_names(base_files.keys(), head_files.keys())
        self.names: dict[Side, dict[str, str]] = {"base": base_names, "head": head_names}
        self.blobs: Mapping[str, bytes] = {}
        self.changes: list[SymbolChange] = []
        self.unanalysed: list[Unanalysed] = []
        self.gone: list[tuple[Symbol, str]] = []  # base symbols not on head: (symbol, file)
        self.new: list[tuple[Symbol, str]] = []  # head symbols not on base

    def blob(self, path: str | None, side: Side) -> str | None:
        """The blob of a Python file on one side (a symlink or a submodule is not one)."""
        if path is None or not path.endswith(".py"):
            return None
        return self.files[side].get(path)

    def table(self, path: str | None, side: Side) -> SymbolTable | None:
        blob = self.blob(path, side)
        if path is None or blob is None:
            return None
        module = self.names[side].get(path)
        if module is None:
            self.unanalysed.append(Unanalysed(path, side, "has no unique importable module name"))
            return None
        # compile()'s documented failures for bytes source: SyntaxError (null bytes included,
        # since 3.12), MemoryError or RecursionError if too complex, OverflowError if too large.
        try:
            return build_table(self.blobs[blob], module)
        except SyntaxError as exc:
            where = f"line {exc.lineno}: " if exc.lineno else ""
            self.unanalysed.append(Unanalysed(path, side, f"does not parse ({where}{exc.msg})"))
        except (MemoryError, RecursionError):
            self.unanalysed.append(Unanalysed(path, side, "too complex to parse"))
        except OverflowError:
            self.unanalysed.append(Unanalysed(path, side, "too large to parse"))
        return None

    def file(self, old_path: str | None, new_path: str | None) -> None:
        reported = len(self.unanalysed)
        base = self.table(old_path, "base")
        head = self.table(new_path, "head")
        if len(self.unanalysed) > reported:
            return  # a side could not be read: reported, and its symbols are not guessed
        old_file, new_file = str(old_path), str(new_path)
        if base is not None and head is not None:
            pairing = _pair(base, head)
            if base.module != head.module:
                self.renamed_file(pairing, new_file)
            else:
                self.changes.extend(
                    _change("modified", new_file, old=old, new=new)
                    for old, new, same in pairing.pairs
                    if not same
                )
            gone, new_symbols = pairing.gone, pairing.new
        else:
            gone = list(base.symbols) if base is not None else []
            new_symbols = list(head.symbols) if head is not None else []
        self.gone.extend((symbol, old_file) for symbol in gone if _has_own_code(symbol))
        self.new.extend((symbol, new_file) for symbol in new_symbols if _has_own_code(symbol))

    def renamed_file(self, pairing: _Pairing, new_file: str) -> None:
        """Every paired symbol of a renamed file moved with it, changed or not."""
        for old, new, _same in pairing.pairs:
            if _has_own_code(old) or _has_own_code(new):
                self.changes.append(_change("moved", new_file, old=old, new=new))

    def match(self) -> None:
        """Pairs symbols gone from base with new ones on head: renames and moves."""
        candidates = []
        for gi, (old, _) in enumerate(self.gone):
            if old.qualname == MODULE_QUALNAME:
                continue
            for ni, (new, _) in enumerate(self.new):
                if new.qualname == MODULE_QUALNAME:
                    continue
                score = _similarity(old, new)
                if score >= SIMILAR:
                    candidates.append((score, gi, ni))
        matched = _greedy(candidates)
        for gi, ni in matched:
            (old, _), (new, new_path) = self.gone[gi], self.new[ni]
            kind: ChangeKind = "renamed" if old.module == new.module else "moved"
            self.changes.append(_change(kind, new_path, old=old, new=new))
        used_gone = {gi for gi, _ in matched}
        used_new = {ni for _, ni in matched}
        for ni, (new, path) in enumerate(self.new):
            if ni not in used_new:
                self.changes.append(_change("added", path, new=new))
        for gi, (old, path) in enumerate(self.gone):
            if gi not in used_gone:
                self.changes.append(_change("removed", path, old=old))


def changed_symbols(
    file_changes: Sequence[FileChange],
    read: Callable[[Sequence[str]], Mapping[str, bytes]],
    *,
    base_files: Files,
    head_files: Files,
) -> ChangedSymbols:
    """The symbols ``file_changes`` (C05's diff) touched; ``read`` gives blobs' bytes by sha."""
    collector = _Collector(base_files, head_files)
    pairs = [(change.old_path, change.new_path) for change in file_changes]
    in_diff = {path for pair in pairs for path in pair if path is not None}
    base_names, head_names = collector.names["base"], collector.names["head"]
    pairs.extend(
        (path, path)
        for path in sorted((base_names.keys() & head_names.keys()) - in_diff)
        if base_names[path] != head_names[path]
    )
    shas = [
        sha
        for old_path, new_path in pairs
        for sha in (collector.blob(old_path, "base"), collector.blob(new_path, "head"))
        if sha is not None
    ]
    collector.blobs = read(shas)
    for old_path, new_path in pairs:
        collector.file(old_path, new_path)
    collector.match()
    order = {"modified": 0, "renamed": 1, "moved": 1, "added": 2, "removed": 3}
    changes = sorted(
        collector.changes,
        key=lambda c: (c.file, (c.head_lines or c.base_lines or (0, 0))[0], order[c.change]),
    )
    return ChangedSymbols(tuple(changes), tuple(collector.unanalysed))


def changed_symbols_between(git: Git, base: str, head: str) -> ChangedSymbols:
    """``changed_symbols`` for two commits of a repository."""
    return changed_symbols(
        diff(git, base, head),
        lambda shas: read_blobs(git, shas),
        base_files=regular_files(git, base),
        head_files=regular_files(git, head),
    )
