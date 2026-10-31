"""Module names and symbol tables (C06)."""

import re
import textwrap

import pytest

from repro.analysis import build_table, module_names
from repro.analysis.symbols import body_only

SYMBOL_ID = re.compile(r"^[^\s:]+:[^\s#]+#[0-9]+$")  # the schema's SymbolId pattern


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["pkg/__init__.py", "pkg/sub/__init__.py", "pkg/sub/mod.py"], "pkg.sub.mod"),
        (["pkg/__init__.py"], "pkg"),
        # A package below directories that are not packages: a monorepo, the src layout.
        (["app/src/rp/__init__.py", "app/src/rp/io/__init__.py", "app/src/rp/io/s.py"], "rp.io.s"),
        (["src/pkg/__init__.py", "src/pkg/mod.py"], "pkg.mod"),
        (["src/__init__.py", "src/pkg/__init__.py", "src/pkg/mod.py"], "src.pkg.mod"),
        # Not in a package: its directory is its root, as pytest's prepend mode imports it.
        (["tests/test_dates.py"], "test_dates"),
        (["tests/__init__.py", "tests/test_dates.py"], "tests.test_dates"),
        (["setup.py"], "setup"),
        (["pkg/__init__.py", "pkg/my-module.py"], None),
        (["__init__.py"], None),
        (["README.md"], None),
    ],
)  # fmt: skip
def test_module_names(files: list[str], expected: str | None) -> None:
    base, head = module_names(files, files)
    assert base == head
    assert base.get(files[-1]) == expected


def test_shared_names_fall_back_to_the_whole_path_on_both_sides() -> None:
    base, head = module_names(["scripts/util.py"], ["scripts/util.py", "tools/util.py"])
    assert base == {"scripts/util.py": "scripts.util"}  # stable, though only head collides
    assert head == {"scripts/util.py": "scripts.util", "tools/util.py": "tools.util"}


def test_a_name_that_stays_ambiguous_is_none() -> None:
    base, _ = module_names(["pkg.py", "pkg/__init__.py", "pkg/mod.py"], [])
    assert base == {"pkg/mod.py": "pkg.mod"}  # pkg.py and pkg/__init__.py both claim "pkg"


SOURCE = textwrap.dedent(
    '''\
    """Module docstring."""
    import functools

    LIMIT = 3  # a constant


    @functools.cache
    def parse(text):
        def clean(value):
            return value.strip()

        return clean(text)


    class Clock:
        """A clock."""

        rate = 1

        @property
        def now(self):
            return 0

        @now.setter
        def now(self, value):
            self._now = value

        async def tick(self):
            class Tock:
                pass

            return Tock


    if LIMIT:

        def fallback():
            return None
    '''
).encode()


def test_every_definition_has_a_stable_id_and_kind() -> None:
    table = build_table(SOURCE, "pkg.clock")
    found = {symbol.id: symbol.kind for symbol in table.symbols}
    assert found == {
        "pkg.clock:<module>#0": "module",
        "pkg.clock:parse#0": "function",
        "pkg.clock:parse.<locals>.clean#0": "function",
        "pkg.clock:Clock#0": "class",
        "pkg.clock:Clock.now#0": "method",
        "pkg.clock:Clock.now#1": "method",
        "pkg.clock:Clock.tick#0": "method",
        "pkg.clock:Clock.tick.<locals>.Tock#0": "class",
        "pkg.clock:fallback#0": "function",  # defined in an if block: still top level
    }
    assert all(SYMBOL_ID.match(symbol_id) for symbol_id in found)


def line_of(text: str) -> int:
    return SOURCE.decode().splitlines().index(text) + 1


@pytest.mark.parametrize(
    ("line", "owner"),
    [
        ("import functools", "<module>"),
        ("LIMIT = 3  # a constant", "<module>"),
        ("@functools.cache", "parse"),  # a decorator belongs to what it decorates
        ("def parse(text):", "parse"),
        ("        return value.strip()", "parse.<locals>.clean"),
        ("    return clean(text)", "parse"),
        ("    rate = 1", "Clock"),  # the class body, outside its methods
        ('    """A clock."""', "Clock"),
        ("    @now.setter", "Clock.now"),
        ("        self._now = value", "Clock.now"),
        ("            pass", "Clock.tick.<locals>.Tock"),
        ("        return None", "fallback"),
    ],
)
def test_lines_belong_to_their_innermost_symbol(line: str, owner: str) -> None:
    table = build_table(SOURCE, "pkg.clock")
    found = table.owner(line_of(line))
    assert found is not None
    assert found.qualname == owner


def test_the_setter_is_the_second_now() -> None:
    table = build_table(SOURCE, "pkg.clock")
    found = table.owner(line_of("        self._now = value"))
    assert found is not None
    assert found.ordinal == 1


def test_blank_and_comment_lines_are_not_code() -> None:
    source = b"x = 1\n\n# just a comment\ny = 2  # trailing comment\n"
    table = build_table(source, "m")
    assert table.code_lines == frozenset({1, 4})


def test_a_multi_line_string_covers_all_its_lines() -> None:
    source = b'TEXT = """\none\n\ntwo\n"""\n'
    assert build_table(source, "m").code_lines == frozenset({1, 2, 3, 4, 5})


def test_spans_start_at_the_first_decorator() -> None:
    table = build_table(SOURCE, "pkg.clock")
    parse = next(s for s in table.symbols if s.qualname == "parse")
    assert (parse.start, parse.end) == (
        line_of("@functools.cache"),
        line_of("    return clean(text)"),
    )


def test_source_that_does_not_parse_raises() -> None:
    with pytest.raises(SyntaxError):
        build_table(b"def broken(:\n    pass\n", "m")


def test_an_empty_module_has_only_its_module_symbol() -> None:
    table = build_table(b"", "m")
    assert [symbol.id for symbol in table.symbols] == ["m:<module>#0"]
    assert table.code_lines == frozenset()


def test_names_do_not_change_the_fingerprint() -> None:
    a = build_table(b"def first(x):\n    return x + 1\n", "m").symbols[1]
    b = build_table(b"def second(x):\n    return x + 1\n", "m").symbols[1]
    c = build_table(b"def first(x):\n    return x - 1\n", "m").symbols[1]
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint


BODIES = b'''"""Classes of every shape."""
import dataclasses
import enum
import typing


class Kind(enum.Enum):
    A = 1


class Failure(ValueError):
    """Raised when it fails."""


class Shape(typing.Protocol):
    sides: int


@dataclasses.dataclass
class Point:
    x: int


class Pair(typing.NamedTuple):
    key: str


class Tuple(NamedTuple):
    key: str


class Sized:
    def size(self):
        return 1


class Sorted:
    key = lambda item: item  # noqa: E731


class Outer:
    class Inner:
        def m(self):
            pass


def make():
    class Local:
        pass

    return Local
'''


@pytest.mark.parametrize(
    ("qualname", "expected"),
    [
        ("Kind", True),  # an enum's members
        ("Failure", True),  # an exception class
        ("Shape", True),  # a Protocol's annotations
        ("Point", False),  # a decorator writes its methods (dataclasses, attrs)
        ("Pair", False),  # NamedTuple writes __new__
        ("Tuple", False),
        ("Sized", False),  # a method of its own
        ("Sorted", False),  # a lambda is code that runs later too
        ("Outer", False),  # a nested class's method counts
        ("Outer.Inner", False),
        ("make.<locals>.Local", False),  # its body runs in make()'s calls: the map sees it
        ("make", False),  # not a class
        ("<module>", False),
    ],
)
def test_a_class_that_is_only_a_body(qualname: str, expected: bool) -> None:
    table = build_table(BODIES, "m")
    (symbol,) = [s for s in table.symbols if s.qualname == qualname]
    assert body_only(symbol) is expected
