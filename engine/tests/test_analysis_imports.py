"""Import graphs and blast radius (C07).

Roadmap C07: "on fixture repos and one pinned real repo, the blast radius matches hand-checked
expectations".
"""

import os
from pathlib import Path

import pytest

from gitrepo import Repo
from repro.analysis import ImportGraph, analyse, graph_payloads, import_graph, module_names
from repro.analysis.blast import selection_graph
from repro.analysis.changes import SymbolChange
from repro.analysis.imports import DEFAULT_TIMEOUT
from repro.gitops import Git, GitError
from repro.gitops.snapshot import case_insensitive
from repro_events.models import BlastComputedPayload, GraphEdgePayload, GraphNodePayload

SHOP = {
    "src/shop/__init__.py": "from shop.cart import Cart\n",
    "src/shop/cart.py": "from . import prices\nfrom .tax import rate\n\n\nclass Cart:\n    pass\n",
    "src/shop/prices.py": "import shop.tax\n\n\ndef total():\n    return shop.tax.rate()\n",
    # An import inside a function still counts.
    "src/shop/tax.py": (
        "def rate():\n    from shop.util.money import round2\n\n    return round2(1)\n"
    ),
    "src/shop/util/__init__.py": "",
    "src/shop/util/money.py": (
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from shop.cart import Cart\n"
    ),
    "src/shop/util/star.py": "from shop.util.money import *\nfrom .. import prices\n",
    # Outside any package: grimp cannot load these, ast reads them.
    "tests/test_cart.py": "from shop.cart import Cart\nimport shop.prices.total\n",
    "tests/conftest.py": "import shop\nfrom . import nothing  # no package: not importable\n",
    "scripts/report.py": "from shop.util import money\nimport json\n",
    "README.md": "not Python\n",
}
SHOP_IMPORTS = {
    ("shop", "shop.cart"),
    ("shop.cart", "shop.prices"),
    ("shop.cart", "shop.tax"),  # from .tax import rate: rate is not a module
    ("shop.prices", "shop.tax"),
    ("shop.tax", "shop.util.money"),
    ("shop.util.money", "shop.cart"),
    ("shop.util.star", "shop.util.money"),
    ("shop.util.star", "shop.prices"),
    ("test_cart", "shop.cart"),
    ("test_cart", "shop.prices"),  # shop.prices.total is a function: its module
    ("conftest", "shop"),
    ("report", "shop.util.money"),
}


def graph_of(
    files: dict[str, str], workdir: Path, *, timeout: float = DEFAULT_TIMEOUT
) -> ImportGraph:
    sources = {path: content.encode() for path, content in files.items() if path.endswith(".py")}
    names, _ = module_names(sources, sources)
    return import_graph(sources, names, workdir, timeout=timeout)


def test_imports_resolve_inside_and_outside_packages(tmp_path: Path) -> None:
    graph = graph_of(SHOP, tmp_path)
    assert graph.problems == ()
    assert graph.imports == SHOP_IMPORTS
    assert graph.modules["shop.util.money"] == "src/shop/util/money.py"
    assert graph.modules["test_cart"] == "tests/test_cart.py"
    assert not any(tmp_path.iterdir())  # grimp's tree is removed afterwards


def test_no_target_code_runs_even_under_shadowing_names(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    run_me = f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
    files = {
        # Named like a module the worker has already imported (stdlib) and like grimp itself.
        "logging/__init__.py": run_me,
        "logging/handlers.py": "from logging import config\n" + run_me,
        "logging/config.py": run_me,
        "grimp/__init__.py": run_me,
        "grimp/core.py": "from grimp.util import x\n" + run_me,
        "grimp/util.py": run_me,
    }
    graph = graph_of(files, tmp_path / "work")
    assert graph.imports == {("logging.handlers", "logging.config"), ("grimp.core", "grimp.util")}
    assert not marker.exists()


def test_unparsable_files_are_reported_and_the_rest_kept(tmp_path: Path) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "from pkg import b\n",
        "pkg/b.py": "",
        "pkg/broken.py": "from pkg import a\ndef broken(:\n",
        "tests/test_x.py": "import pkg.a\ndef broken(:\n",
        "tests/test_y.py": "import pkg.b\n",
    }
    graph = graph_of(files, tmp_path)
    reasons = {problem.subject: problem.reason for problem in graph.problems}
    assert reasons["pkg/broken.py"].startswith("grimp cannot parse it (line ")
    assert reasons["tests/test_x.py"].startswith("does not parse (line 2: ")
    assert graph.imports == {("pkg.a", "pkg.b"), ("test_y", "pkg.b")}


def test_paths_that_cannot_be_written_safely_are_reported(tmp_path: Path) -> None:
    files = {
        "con/__init__.py": "",  # a Windows device name: the package is not analysed
        "pkg/__init__.py": "",
        "pkg/aux.py": "from pkg import ok\n",  # a device name again: this file only
        "pkg/ok.py": "",
        "pkg/user.py": "from pkg import ok\n",
    }
    graph = graph_of(files, tmp_path)
    reasons = {problem.subject: problem.reason for problem in graph.problems}
    assert reasons["con"] == "package not analysed: a Windows device name"
    assert reasons["pkg/aux.py"] == "not analysed: a Windows device name"
    assert graph.imports == {("pkg.user", "pkg.ok")}


def test_names_differing_in_case_are_reported_where_they_collide(tmp_path: Path) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/Mod.py": "",
        "pkg/mod.py": "",
        "pkg/u.py": "import pkg.mod\n",
    }
    graph = graph_of(files, tmp_path / "work")
    if case_insensitive(tmp_path):
        reasons = {problem.subject: problem.reason for problem in graph.problems}
        assert reasons["pkg/mod.py"] == (
            "not analysed: collides with 'pkg/Mod.py' on this case-insensitive filesystem"
        )
    else:
        assert graph.problems == ()
        assert graph.imports == {("pkg.u", "pkg.mod")}


def test_a_slow_grimp_is_reported_and_the_other_modules_kept(tmp_path: Path) -> None:
    graph = graph_of(SHOP, tmp_path, timeout=0.001)
    assert [p.reason for p in graph.problems] == ["grimp took longer than 0.001 s"]
    assert graph.imports == {edge for edge in SHOP_IMPORTS if not edge[0].startswith("shop")}


APP = {
    "app/__init__.py": "",
    "app/core.py": "def f():\n    return 1\n",
    "app/service.py": "from app.core import f\n\n\ndef g():\n    return f()\n",
    "app/api.py": "from app import service\n",
    "tests/test_api.py": "import app.api\n",
}


@pytest.mark.parametrize(
    ("head", "changed", "affected", "module_level"),
    [
        pytest.param(
            {"app/core.py": "def f():\n    return 2\n"},
            ["app.core:f#0"],
            [("app.service:<module>#0", 1), ("app.api:<module>#0", 2), ("test_api:<module>#0", 3)],
            [],
            id="a function's importers, by distance",
        ),
        pytest.param(
            {"app/api.py": "from app import service\n\nVERSION = 2\n"},
            ["app.api:<module>#0"],
            [("test_api:<module>#0", 1)],
            ["app.api"],
            id="a module-level change",
        ),
        pytest.param(
            {"app/core.py": "", "app/util.py": "def f():\n    return 1\n"},
            ["app.util:f#0"],
            [("app.service:<module>#0", 1), ("app.api:<module>#0", 2), ("test_api:<module>#0", 3)],
            [],
            id="a moved function: the module it left counts too",
        ),
        pytest.param(
            {"app/service.py": "def g():\n    return 1\n"},
            ["app.service:<module>#0", "app.service:g#0"],
            [("app.api:<module>#0", 1), ("test_api:<module>#0", 2)],
            ["app.service"],
            id="a changed module is not also affected",
        ),
    ],
)
def test_blast_radius(
    tmp_path: Path,
    head: dict[str, str],
    changed: list[str],
    affected: list[tuple[str, int]],
    module_level: list[str],
) -> None:
    repo = Repo(tmp_path / "repo")
    base_sha = repo.commit(APP)
    head_sha = repo.commit(head)
    result = analyse(Git(repo.path), base_sha, head_sha, tmp_path / "work")
    assert list(result.blast.changed) == changed
    assert list(result.blast.affected) == affected
    assert list(result.blast.module_level_changes) == module_level
    BlastComputedPayload.model_validate(result.blast.payload())


def test_graph_events_cover_both_sides(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "repo")
    base_sha = repo.commit({**APP, "app/old.py": "import app.core\n"})
    head_sha = repo.commit({"app/old.py": None, "app/core.py": "def f():\n    return 2\n"})
    result = analyse(Git(repo.path), base_sha, head_sha, tmp_path / "work")
    events = graph_payloads(result.base, result.head, result.changes.changes)
    nodes = [p["node"] for kind, p in events if kind == "graph.node"]
    edges = [(p["source"], p["target"]) for kind, p in events if kind == "graph.edge"]
    assert {
        "id": "app.old",
        "type": "module",
        "name": "app.old",
        "file": "app/old.py",
        "line": None,
    } in nodes
    assert {
        "id": "app.core:f#0",
        "type": "function",
        "name": "f",
        "file": "app/core.py",
        "line": 1,
    } in nodes
    assert not any(node["id"].endswith("<module>#0") for node in nodes)
    assert ("app.old", "app.core") in edges  # an import only base had
    assert edges == sorted(edges)
    kinds = [kind for kind, _ in events]
    assert kinds == sorted(kinds, key=lambda k: k != "graph.node")  # nodes before edges
    for kind, payload in events:
        model = GraphNodePayload if kind == "graph.node" else GraphEdgePayload
        model.model_validate(payload)


CHANGES = (
    SymbolChange("app.core:f#0", "modified", "function", "app/core.py", (1, 2), (1, 3)),
    SymbolChange(  # moved: its tests name it by its base ID
        "app.new:g#0", "moved", "function", "app/new.py", (4, 5), (1, 2), "app.old:g#0"
    ),
    SymbolChange("app.core:<module>#0", "modified", "module", "app/core.py", (1, 1), (1, 1)),
)


def test_selected_tests_become_nodes_with_covers_edges_to_changed_symbols() -> None:
    selected = [
        ("tests/test_core.py::TestF::test_one", ["app.core:f#0", "app.old:g#0"]),
        ("tests/test_core.py::test_two", ["app.core:<module>#0"]),  # module-level: no node
        ("tests/test_core.py::test_two", ["app.core:f#0"]),  # again (a second map): once
        ("tests/test_new.py::test_own", ["tests.test_new:test_own#0"]),  # not a node
    ]
    found = selection_graph(selected, CHANGES)
    nodes = [p["node"] for kind, p in found.events if kind == "graph.node"]
    edges = [
        (p["source"], p["target"], p["type"]) for kind, p in found.events if kind != "graph.node"
    ]
    assert nodes == [
        {
            "id": "tests/test_core.py::TestF::test_one",
            "type": "test",
            "name": "TestF.test_one",
            "file": "tests/test_core.py",
            "line": None,
        },
        {
            "id": "tests/test_core.py::test_two",
            "type": "test",
            "name": "test_two",
            "file": "tests/test_core.py",
            "line": None,
        },
        {
            "id": "tests/test_new.py::test_own",
            "type": "test",
            "name": "test_own",
            "file": "tests/test_new.py",
            "line": None,
        },
    ]
    assert edges == [
        ("tests/test_core.py::TestF::test_one", "app.core:f#0", "covers"),
        ("tests/test_core.py::TestF::test_one", "app.new:g#0", "covers"),  # its node's ID
        ("tests/test_core.py::test_two", "app.core:f#0", "covers"),
    ]
    assert found.left_out == 0
    kinds = [kind for kind, _ in found.events]
    assert kinds == sorted(kinds, key=lambda k: k != "graph.node")  # nodes before edges
    for kind, payload in found.events:
        model = GraphNodePayload if kind == "graph.node" else GraphEdgePayload
        model.model_validate(payload)


def test_a_large_selection_keeps_the_tests_selected_for_the_most_symbols() -> None:
    selected = [(f"tests/test_x.py::test_{i:02}", ["app.core:f#0"]) for i in range(10)]
    selected.append(("tests/test_x.py::test_both", ["app.core:f#0", "app.old:g#0"]))
    selected.append(("tests/" + "x" * 1100 + ".py::test_long", ["app.core:f#0"]))  # too long
    found = selection_graph(selected, CHANGES, limit=3)
    nodes = [p["node"]["id"] for kind, p in found.events if kind == "graph.node"]
    assert nodes == [
        "tests/test_x.py::test_00",
        "tests/test_x.py::test_01",
        "tests/test_x.py::test_both",
    ]
    assert found.left_out == 9
    sources = {p["source"] for kind, p in found.events if kind == "graph.edge"}
    assert sources == set(nodes)  # no edge from a test without a node


# This repository's own history, pinned: C05 (#33) changed repro.api.stream. Each distance was
# checked by hand against the import lines at 929b9eb: app imports stream; repro.api imports
# app; cli (inside `serve`) and the three API test modules import repro.api; __main__ and
# test_cli import cli.
PINNED = ("705a9aa", "929b9eb")
PINNED_STREAM_IMPORTERS = [
    ("repro.api.app:<module>#0", 1),
    ("repro.api:<module>#0", 2),
    ("repro.cli:<module>#0", 3),
    ("test_api_rest:<module>#0", 3),
    ("test_api_security:<module>#0", 3),
    ("test_api_stream:<module>#0", 3),
    ("repro.__main__:<module>#0", 4),
    ("test_cli:<module>#0", 4),
]


def test_blast_radius_on_a_pinned_real_repository(tmp_path: Path) -> None:
    git = Git(Path(__file__).resolve().parents[2])
    try:
        base, head = (
            git.text("rev-parse", "--verify", f"{sha}^{{commit}}").strip() for sha in PINNED
        )
    except GitError:
        if os.environ.get("CI"):
            raise  # CI checks out the full history: the pinned commits must be there
        pytest.skip("the pinned commits are not in this clone (a shallow clone?)")
    result = analyse(git, base, head, tmp_path)
    assert result.base.problems == result.head.problems == ()
    assert list(result.blast.affected) == PINNED_STREAM_IMPORTERS
    assert "repro.api.stream:stream_run#0" in result.blast.changed
    assert "repro.api.stream" in result.blast.module_level_changes
