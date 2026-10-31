"""Changed lines to changed symbols, table-driven: one fixture repository per case (C06).

Roadmap C06: "fixture repos with edits, renames, moves, decorators and class-body changes map
correctly".
"""

import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from gitrepo import Entry, Repo
from repro.analysis import changed_symbols_between
from repro.gitops import Git
from repro_events.models import SymbolChangedPayload


def code(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


BASE_A = code('''
    """Dates."""
    import functools

    LIMIT = 3


    @functools.cache
    def parse(text):
        return text.strip()


    def tidy(value):
        return value.lower()


    class Clock:
        rate = 1

        def tick(self):
            return self.rate

        @property
        def now(self):
            return 0

        @now.setter
        def now(self, value):
            self._now = value


    def outer():
        def inner():
            return 1

        return inner
''')


PACKAGE = {"pkg/__init__.py": ""}  # every fixture's pkg/ is a regular package


def edit(old: str, new: str) -> str:
    assert BASE_A.count(old) == 1, old
    return BASE_A.replace(old, new)


def overloads(*types: str, body: str = "return x") -> str:
    """A module with one overload of ``coerce`` per type, then its implementation."""
    stubs = "".join(f"@overload\ndef coerce(x: {t}) -> {t}: ...\n" for t in types)
    return f"from typing import overload\n\n\n{stubs}def coerce(x):\n    {body}\n"


@dataclass(frozen=True)
class Case:
    name: str
    head: dict[str, Entry | None]
    expected: set[tuple[str, str, str | None]]  # (change, symbol_id, previous_symbol_id)
    base: dict[str, str] | None = None


CASES = [
    Case(
        "an edit in a function body",
        {"pkg/a.py": edit("return text.strip()", "return text.strip().upper()")},
        {("modified", "pkg.a:parse#0", None)},
    ),
    Case(
        "an edit to a decorator",
        {"pkg/a.py": edit("@functools.cache", "@functools.lru_cache(2)")},
        {("modified", "pkg.a:parse#0", None)},
    ),
    Case(
        "a class-body change",
        {"pkg/a.py": edit("    rate = 1", "    rate = 2")},
        {("modified", "pkg.a:Clock#0", None)},
    ),
    Case(
        "a method change is the method, not the class",
        {"pkg/a.py": edit("return self.rate", "return self.rate * 2")},
        {("modified", "pkg.a:Clock.tick#0", None)},
    ),
    Case(
        "a property setter is the second definition",
        {"pkg/a.py": edit("self._now = value", "self._now = int(value)")},
        {("modified", "pkg.a:Clock.now#1", None)},
    ),
    Case(
        "a nested function",
        {"pkg/a.py": edit("        return 1", "        return 2")},
        {("modified", "pkg.a:outer.<locals>.inner#0", None)},
    ),
    Case(
        "module-level code",
        {"pkg/a.py": edit("LIMIT = 3", "LIMIT = 4")},
        {("modified", "pkg.a:<module>#0", None)},
    ),
    Case(
        "a docstring is code",
        {"pkg/a.py": edit('"""Dates."""', '"""Dates and times."""')},
        {("modified", "pkg.a:<module>#0", None)},
    ),
    Case(
        "comments and blank lines change nothing",
        {"pkg/a.py": edit("LIMIT = 3\n", "LIMIT = 3  # a limit\n\n# more\n")},
        set(),
    ),
    Case(
        "a reformat changes nothing",
        {
            "pkg/a.py": edit(
                "return text.strip()", "return (\n        text.strip()\n    )"
            ).replace("LIMIT = 3", "LIMIT=3")
        },
        set(),
    ),
    Case(
        "an added method is the method alone",
        {
            "pkg/a.py": edit(
                "    @property\n", "    def reset(self):\n        self.rate = 0\n\n    @property\n"
            )
        },
        {("added", "pkg.a:Clock.reset#0", None)},
    ),
    Case(
        "a nested definition moved after its use changes its parent",
        {
            "pkg/a.py": edit(
                "    def inner():\n        return 1\n\n    return inner\n",
                "    return inner\n\n    def inner():\n        return 1\n",
            )
        },
        {("modified", "pkg.a:outer#0", None)},
    ),
    Case(
        "an added function",
        {"pkg/a.py": BASE_A + "\n\ndef added(x):\n    return x\n"},
        {("added", "pkg.a:added#0", None)},
    ),
    Case(
        "a removed function",
        {"pkg/a.py": edit("def tidy(value):\n    return value.lower()\n\n\n", "")},
        {("removed", "pkg.a:tidy#0", None)},
    ),
    Case(
        "a rename keeps its identity",
        {"pkg/a.py": edit("def tidy(value):", "def normalise(value):")},
        {("renamed", "pkg.a:normalise#0", "pkg.a:tidy#0")},
    ),
    Case(
        "a rename with its parameter renamed too",
        {
            "pkg/a.py": edit(
                "def tidy(value):\n    return value.lower()",
                "def normalise(v):\n    return v.lower()",
            )
        },
        {("renamed", "pkg.a:normalise#0", "pkg.a:tidy#0")},
    ),
    Case(
        "a rewrite is not a rename",
        {
            "pkg/a.py": edit(
                "def tidy(value):\n    return value.lower()",
                "def shout(items, sep):\n    return sep.join(i.upper() for i in items if i)",
            )
        },
        {("removed", "pkg.a:tidy#0", None), ("added", "pkg.a:shout#0", None)},
    ),
    Case(
        "a function moved to another module",
        {
            "pkg/a.py": edit("def tidy(value):\n    return value.lower()\n\n\n", ""),
            "pkg/b.py": "def tidy(value):\n    return value.lower()\n",
        },
        {("moved", "pkg.b:tidy#0", "pkg.a:tidy#0")},
    ),
    Case(
        "a renamed file moves every symbol",
        {"pkg/a.py": None, "pkg/dates.py": BASE_A},
        {
            ("moved", f"pkg.dates:{name}", f"pkg.a:{name}")
            for name in (
                "<module>#0",
                "parse#0",
                "tidy#0",
                "Clock#0",
                "Clock.tick#0",
                "Clock.now#0",
                "Clock.now#1",
                "outer#0",
                "outer.<locals>.inner#0",
            )
        },
    ),
    Case(
        "a new file",
        {"pkg/new.py": "VERSION = 1\n\n\ndef hello():\n    return VERSION\n"},
        {("added", "pkg.new:<module>#0", None), ("added", "pkg.new:hello#0", None)},
    ),
    Case(
        "a new file of definitions only has no module code",
        {"pkg/new.py": "def hello():\n    return 1\n"},
        {("added", "pkg.new:hello#0", None)},
    ),
    Case(
        "a deleted file",
        {"pkg/b.py": None},
        {("removed", "pkg.b:<module>#0", None), ("removed", "pkg.b:helper#0", None)},
        base={"pkg/a.py": BASE_A, "pkg/b.py": "X = 1\n\n\ndef helper():\n    return X\n"},
    ),
    Case(
        "the src layout",
        {"src/pkg/a.py": edit("return value.lower()", "return value.casefold()")},
        {("modified", "pkg.a:tidy#0", None)},
        base={"src/pkg/__init__.py": "", "src/pkg/a.py": BASE_A},
    ),
    Case(
        "a package below a monorepo directory",
        {"engine/src/dates/a.py": edit("return value.lower()", "return value.casefold()")},
        {("modified", "dates.a:tidy#0", None)},
        base={"engine/src/dates/__init__.py": "", "engine/src/dates/a.py": BASE_A},
    ),
    Case(
        "an __init__.py added above an unchanged file moves its symbols",
        {"lib/__init__.py": ""},
        {("moved", "lib.a:f#0", "a:f#0")},
        base={"lib/a.py": "def f():\n    return 1\n"},
    ),
    Case(
        "a Python symlink is not a module",
        {"pkg/link.py": ("pkg/a.py", "120000")},
        set(),
    ),
    Case(
        "a non-Python file is not a symbol",
        {"README.md": "changed\n"},
        set(),
        base={"pkg/a.py": BASE_A, "README.md": "docs\n"},
    ),
    # Repeated names are aligned by their code, so ordinals that shift are not changes.
    Case(
        "a new overload does not shift the implementation into a change",
        {"pkg/c.py": overloads("int", "str", "bytes")},
        {("added", "pkg.c:coerce#2", None)},
        base={"pkg/c.py": overloads("int", "str")},
    ),
    Case(
        "a removed overload is that overload",
        {"pkg/c.py": overloads("str")},
        {("removed", "pkg.c:coerce#0", None)},
        base={"pkg/c.py": overloads("int", "str")},
    ),
    Case(
        "an edited implementation keeps its identity across a shift",
        {"pkg/c.py": overloads("int", "str", "bytes", body="return x or None")},
        {
            ("added", "pkg.c:coerce#2", None),
            ("modified", "pkg.c:coerce#3", "pkg.c:coerce#2"),
        },
        base={"pkg/c.py": overloads("int", "str")},
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_changed_lines_map_to_symbols(tmp_path: Path, case: Case) -> None:
    repo = Repo(tmp_path / "repo")
    base = repo.commit({**PACKAGE, **(case.base or {"pkg/a.py": BASE_A})})
    head = repo.commit(case.head)
    result = changed_symbols_between(Git(repo.path), base, head)
    found = {(c.change, c.symbol_id, c.previous_symbol_id) for c in result.changes}
    assert found == case.expected
    assert result.unanalysed == ()
    for change in result.changes:
        SymbolChangedPayload.model_validate(change.payload())  # schema-valid symbol.changed


def test_line_spans_on_each_side(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "repo")
    base = repo.commit({**PACKAGE, "pkg/a.py": BASE_A})
    head = repo.commit(
        {"pkg/a.py": "# header\n\n" + BASE_A.replace("return text.strip()", "return text")}
    )
    (change,) = changed_symbols_between(Git(repo.path), base, head).changes
    assert change.symbol_id == "pkg.a:parse#0"
    assert change.kind == "function"
    assert change.base_lines == (7, 9)
    assert change.head_lines == (9, 11)  # shifted by the two new lines above it
    assert change.payload()["base_lines"] == [7, 9]


def test_a_class_is_only_a_body_when_it_is_one_on_every_side(tmp_path: Path) -> None:
    """Test selection treats such a class as module-level code (``symbols.body_only``)."""
    repo = Repo(tmp_path / "repo")
    enum = "import enum\n\n\nclass Kind(enum.Enum):\n    A = 1\n"
    base = repo.commit({**PACKAGE, "pkg/kinds.py": enum, "pkg/more.py": enum})
    head = repo.commit(
        {
            "pkg/kinds.py": enum + "    B = 2\n",
            "pkg/more.py": enum + "    B = 2\n\n    def label(self):\n        return 'b'\n",
            "pkg/new.py": "class Failure(ValueError):\n    pass\n",
        }
    )
    found = changed_symbols_between(Git(repo.path), base, head).changes
    shapes = {c.symbol_id: c.body_only for c in found if c.kind == "class"}
    assert shapes == {"pkg.kinds:Kind#0": True, "pkg.more:Kind#0": False, "pkg.new:Failure#0": True}
    assert "body_only" not in found[0].payload()  # not part of the event


def test_files_that_cannot_be_read_are_reported_not_guessed(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "repo")
    deep = "x = " + "+".join(["1"] * 20_000) + "\n"  # parses, but too deep to build an AST
    base = repo.commit(
        {
            **PACKAGE,
            "pkg/a.py": BASE_A,
            "pkg/b.py": "def broken(:\n",
            "pkg/d.py": "x = 1\n",
            "pkg/e.py": "x = 1\n",
            "t/my t.py": "",
        }
    )
    head = repo.commit(
        {
            "pkg/a.py": "def broken(:\n",
            "pkg/b.py": "y = 1\n",
            "pkg/d.py": deep,
            "pkg/e.py": b"x = 1\x00\n",  # git calls it binary; it is still a changed module
            "t/my t.py": "1\n",
        }
    )
    result = changed_symbols_between(Git(repo.path), base, head)
    reasons = {(item.path, item.side): item.reason for item in result.unanalysed}
    assert set(reasons) == {
        ("pkg/a.py", "head"),
        ("pkg/b.py", "base"),
        ("pkg/d.py", "head"),
        ("pkg/e.py", "head"),
        ("t/my t.py", "base"),
        ("t/my t.py", "head"),
    }
    assert reasons[("pkg/a.py", "head")].startswith("does not parse (line 1: ")
    assert reasons[("pkg/d.py", "head")] == "too complex to parse"
    assert reasons[("pkg/e.py", "head")].startswith("does not parse (")
    assert "null bytes" in reasons[("pkg/e.py", "head")]  # CPython's wording, after the prefix
    assert reasons[("t/my t.py", "head")] == "has no unique importable module name"
    # The readable side of each file is not reported as added or removed wholesale.
    assert result.changes == ()
