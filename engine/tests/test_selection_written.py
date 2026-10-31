"""The map's view of methods code writes for a class (D05), run for real on Python 3.12 to 3.15.

dataclasses, attrs and ``NamedTuple`` compile the methods they write from strings, so those
methods have no file of their own (``<string>``). The plugin maps such a method as its class:
the suite (``data/written_suite``) constructs one class of each kind per test, and each test's
map must name that class. Classes whose methods nothing writes (an enum's lookup runs the
standard library's code) and code compiled from a string outside any class name nothing more.
"""

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from repro.provision import HostProvisioner, inspect_project
from repro.runner import run_tests, selection
from repro.sandbox import Limits, ProcessSandbox

SUITE = Path(__file__).parent / "data" / "written_suite"

EXPECTED = {
    "test_dataclass": {"shapes:Point#0"},  # __init__ and __repr__
    "test_frozen_slots": {"shapes:Frozen#0"},  # slots=True makes a new class: its own dict
    "test_empty": {"shapes:Empty#0"},  # __init__(self) and __eq__
    "test_named_tuple": {"shapes:Pair#0"},  # __new__, which receives the class
    # A namedtuple() call is module-level code: Row has no class statement, so no symbol.
    "test_functional_namedtuple": {"shapes:<module>#0"},
    "test_record": {"shapes:Span#0"},  # an attrs-style decorator's __init__
    "test_dataclass_subclass": {"shapes:Labelled#0"},
    "test_plain_subclass": {"shapes:Point#0"},  # Moved runs the __init__ written for Point
    "test_enum": set(),  # Colour(1) runs only enum's own code
    "test_exec": {"shapes:snippet#0"},  # twice() is compiled from a string, in no class
}


def resolver(root: Path) -> selection.Resolver:
    names = {"shapes.py": "shapes", "test_shapes.py": "test_shapes"}

    def read(paths: Sequence[str]) -> dict[str, bytes]:
        return {path: (root / path).read_bytes() for path in paths if (root / path).is_file()}

    return selection.Resolver(names, read)


def mapped(provisioner: HostProvisioner, root: Path) -> dict[str, set[str]]:
    snapshot = root / "snapshot"
    shutil.copytree(SUITE, snapshot)
    box = ProcessSandbox(root / "work", Limits(timeout_s=300))
    env = provisioner.provision("base", inspect_project(snapshot), box)
    run = run_tests(box, env, map_tests=True)
    assert run.results.complete, run.execution.stdout.decode(errors="replace")
    assert set(run.results.outcomes().values()) == {"passed"}
    built = selection.test_map(run.results, resolver(snapshot))
    return {
        test.rpartition("::")[2]: {s for s in ran if not s.startswith("test_shapes:")}
        for test, ran in built.ran.items()
    }


@pytest.fixture(scope="module")
def cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("envs")


@pytest.mark.parametrize("python", ["3.12", "3.13", "3.14", "3.15"])
def test_written_methods_are_mapped_as_their_class(
    cache: Path, tmp_path: Path, python: str
) -> None:
    assert mapped(HostProvisioner(cache, python=python), tmp_path) == EXPECTED
