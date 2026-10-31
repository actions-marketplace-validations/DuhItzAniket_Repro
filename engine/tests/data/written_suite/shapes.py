"""Classes whose methods code writes for them, and some whose methods nothing writes."""

from collections import namedtuple
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple


@dataclass
class Point:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class Frozen:
    name: str


@dataclass(frozen=True, slots=True)
class Empty:
    """No fields: its written __init__ takes only self."""


class Pair(NamedTuple):
    key: str
    value: str


Row = namedtuple("Row", "a b")


def record(*fields):
    """Writes a class's __init__ from a string, as attrs does."""

    def build(cls):
        body = "".join(f"    self.{name} = {name}\n" for name in fields) or "    pass\n"
        source = f"def __init__(self, {', '.join(fields)}):\n{body}"
        namespace = {}
        exec(compile(source, f"<record generated init {cls.__qualname__}>", "exec"), namespace)
        cls.__init__ = namespace["__init__"]
        return cls

    return build


@record("start", "end")
class Span:
    pass


@dataclass
class Labelled(Point):
    label: str = ""


class Moved(Point):
    pass


class Colour(Enum):
    RED = 1


def snippet():
    namespace = {}
    exec("def twice(x):\n    return 2 * x\n", namespace)
    return namespace["twice"](2)
