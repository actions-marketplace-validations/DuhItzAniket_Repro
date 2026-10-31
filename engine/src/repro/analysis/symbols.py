"""A module's symbol table, from its source parsed with ``ast`` (ADR-0006: never imported or run).

Every function, method and class gets an identity stable across refs, the event schema's
``SymbolId``: ``<module>:<qualname>#<ordinal>``. The qualified name is Python's own
``__qualname__`` form (``Class.method``, ``outer.<locals>.inner``); the ordinal tells repeated
names apart in source order (a property's getter is ``#0``, its setter ``#1``). The module's own
code is the symbol ``<module>``.

A symbol spans from its first decorator to its last line, and **owns** the lines of that span
that no nested symbol owns: a class attribute belongs to the class, a method line to the method,
an import or a constant to ``<module>``. Code lines are those holding a token other than a
comment, an indent or a line break. Ownership attributes lines (a diff hunk, an executed line)
to symbols.

A symbol's **own code** is its AST without positions or comments, each nested definition reduced
to a marker naming it: what decides whether a symbol changed. A reformat or a new comment leaves
it equal; a changed method changes the method, not its class.
"""

import ast
import io
import tokenize
from collections import Counter
from collections.abc import Container, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Final, Literal

SymbolKind = Literal["module", "class", "function", "method"]
SymbolKey = tuple[str, int]  # (qualname, ordinal): a symbol's identity within its module
MODULE_QUALNAME: Final = "<module>"
_NOT_CODE: Final = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)  # no slots: ``fingerprint`` is cached on the instance
class Symbol:
    module: str
    qualname: str
    ordinal: int
    kind: SymbolKind
    start: int
    end: int
    node: ast.AST | None = field(default=None, compare=False, repr=False)

    @property
    def id(self) -> str:
        return f"{self.module}:{self.qualname}#{self.ordinal}"

    @property
    def key(self) -> SymbolKey:
        return (self.qualname, self.ordinal)

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]

    @cached_property
    def fingerprint(self) -> tuple[str, ...]:
        """The whole definition, nested ones included, without its name (for similarity)."""
        return _shape(self.node, {}, ())


@dataclass(frozen=True, slots=True)
class SymbolTable:
    module: str
    symbols: tuple[Symbol, ...]  # the module symbol first, then in source order
    owners: tuple[int, ...]  # owners[line - 1]: the index of the symbol owning that line
    code_lines: frozenset[int]

    def owner(self, line: int) -> Symbol | None:
        """The symbol owning a 1-based line, or ``None`` past the end of the file."""
        if 1 <= line <= len(self.owners):
            return self.symbols[self.owners[line - 1]]
        return None

    def by_key(self) -> dict[SymbolKey, Symbol]:
        return {symbol.key: symbol for symbol in self.symbols}

    def own_code(self, keep: Container[SymbolKey]) -> dict[SymbolKey, tuple[str, ...]]:
        """Each symbol's own code, as tokens. A nested definition is a marker if its key is in
        ``keep`` (both sides have it), else left out: one added or removed is its own change,
        not its parent's."""
        nested = {id(symbol.node): symbol.key for symbol in self.symbols[1:]}
        return {symbol.key: _shape(symbol.node, nested, keep) for symbol in self.symbols}


def _shape(
    root: ast.AST | None, nested: Mapping[int, SymbolKey], keep: Container[SymbolKey]
) -> tuple[str, ...]:
    """``root``'s AST as tokens: no positions (attributes are not fields), its own name ``_``.

    A definition in ``nested`` below the root becomes a marker naming it if ``keep`` holds its
    key, and is left out otherwise.
    """
    tokens: list[str] = []
    # An explicit stack, not recursion: an untrusted file may nest expressions thousands deep.
    stack: list[tuple[bool, object]] = [] if root is None else [(False, root)]  # (token?, value)
    while stack:
        is_token, value = stack.pop()
        if is_token:
            tokens.append(str(value))
        elif isinstance(value, ast.AST):
            key = nested.get(id(value)) if value is not root else None
            if key is not None:
                tokens.append(f"<def {key[0]}#{key[1]}>")
                continue
            tokens.extend((type(value).__name__, "("))
            stack.append((True, ")"))
            for name in reversed(value._fields):
                is_name = value is root and name == "name"
                stack.append((True, "_") if is_name else (False, getattr(value, name, None)))
        elif isinstance(value, list):
            tokens.append("[")
            stack.append((True, "]"))
            for item in reversed(value):
                key = nested.get(id(item))
                if key is None or key in keep:
                    stack.append((False, item))
        else:
            tokens.append(repr(value))
    return tuple(tokens)


_FUNCTIONS: Final = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def body_only(symbol: Symbol) -> bool:
    """Whether a class's body is all its code, so that it runs only when its module is imported:
    no function or lambda in it, no decorator and no ``NamedTuple`` base (dataclasses, attrs and
    ``NamedTuple`` write methods for a class, which run later), and not inside a function (whose
    calls run the body). Such a class (an enum's members, an exception class, a ``Protocol``)
    has nothing a test can be seen to run: its use runs the standard library's code or none."""
    node = symbol.node
    if not isinstance(node, ast.ClassDef) or "<locals>" in symbol.qualname:
        return False
    if node.decorator_list:
        return False
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
        if name == "NamedTuple":
            return False
    return not any(isinstance(n, _FUNCTIONS) for s in node.body for n in ast.walk(s))


def _code_lines(source: bytes) -> frozenset[int]:
    lines: set[int] = set()
    try:
        for token in tokenize.tokenize(io.BytesIO(source).readline):
            if token.type not in _NOT_CODE:
                lines.update(range(token.start[0], token.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        return frozenset(range(1, source.count(b"\n") + 2))  # when unsure, every line counts
    return frozenset(lines)


def build_table(source: bytes, module: str) -> SymbolTable:
    """The symbol table of one module. Raises ``SyntaxError`` if the source does not parse."""
    tree = ast.parse(source, type_comments=False)
    line_count = max(source.count(b"\n") + (0 if source.endswith(b"\n") else 1), 1)
    symbols: list[Symbol] = [Symbol(module, MODULE_QUALNAME, 0, "module", 1, line_count, tree)]
    owners = [0] * line_count
    seen: Counter[str] = Counter()

    def visit(body: list[ast.stmt], prefix: str, in_class: bool) -> None:
        for statement in body:
            if isinstance(statement, Definition):
                qualname = f"{prefix}{statement.name}"
                ordinal = seen[qualname]
                seen[qualname] += 1
                if isinstance(statement, ast.ClassDef):
                    kind: SymbolKind = "class"
                else:
                    kind = "method" if in_class else "function"
                start = min([statement.lineno, *(d.lineno for d in statement.decorator_list)])
                end = statement.end_lineno or statement.lineno
                index = len(symbols)
                symbols.append(Symbol(module, qualname, ordinal, kind, start, end, statement))
                for line in range(start, min(end, line_count) + 1):
                    owners[line - 1] = index  # nested symbols, visited next, take theirs back
                if isinstance(statement, ast.ClassDef):
                    visit(statement.body, f"{qualname}.", in_class=True)
                else:
                    visit(statement.body, f"{qualname}.<locals>.", in_class=False)
                continue
            # Definitions inside if/try/with/for/while/match blocks keep the enclosing scope.
            for block in _blocks(statement):
                visit(block, prefix, in_class)

    visit(tree.body, "", in_class=False)
    return SymbolTable(module, tuple(symbols), tuple(owners), _code_lines(source))


def _blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    """The statement lists nested in a compound statement."""
    blocks: list[list[ast.stmt]] = []
    for name in ("body", "orelse", "finalbody"):
        value = getattr(statement, name, None)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            blocks.append(value)
    blocks.extend(handler.body for handler in getattr(statement, "handlers", []) or [])
    blocks.extend(case.body for case in getattr(statement, "cases", []) or [])
    return blocks
