"""What a run's log must say about its selected tests as graph nodes (B14's strands)."""

from collections import Counter
from typing import Any


def check_selection_graph(events: list[dict[str, Any]]) -> None:
    """Each selected test is one ``test`` node with its test ID, before its ``test.selected``;
    each ``covers`` edge runs from such a node to a changed symbol's node that selected it."""
    nodes = [(i, e["payload"]["node"]) for i, e in enumerate(events) if e["kind"] == "graph.node"]
    ids = Counter(node["id"] for _, node in nodes)
    assert [i for i, n in ids.items() if n > 1] == []  # no duplicates
    tests = {node["id"]: (i, node) for i, node in nodes if node["type"] == "test"}
    selected = {
        e["target"]["test"]: (i, e["payload"]["symbols"])
        for i, e in enumerate(events)
        if e["kind"] == "test.selected"
    }
    assert set(tests) == set(selected)
    changed = {e["payload"]["symbol_id"] for e in events if e["kind"] == "symbol.changed"}
    symbols = {node["id"]: node for _, node in nodes if node["type"] != "test"}
    for test, (at, node) in tests.items():
        assert at < selected[test][0]
        assert node["file"] == test.partition("::")[0]
        assert node["name"] == test.partition("::")[2].replace("::", ".")
    covers = [
        (i, e["payload"]) for i, e in enumerate(events) if e["kind"] == "graph.edge"
        if e["payload"]["type"] == "covers"
    ]  # fmt: skip
    pairs = Counter((edge["source"], edge["target"]) for _, edge in covers)
    assert [pair for pair, n in pairs.items() if n > 1] == []
    for at, edge in covers:
        source, target = edge["source"], edge["target"]
        assert tests[source][0] < at < selected[source][0]
        assert symbols[target]["type"] in ("function", "method", "class")
        assert target in changed
