// B14: the relations strands fire from, and each file's recent activity.
import type { ReproEvent } from "@repro/events";
import { describe, expect, it } from "vitest";
import { CockpitModel } from "../../model/cockpit";
import { renumber, runEvents } from "../../model/test-support";
import { eventFile } from "../ring/sections";
import { streams } from "../streams/style";
import { forceLayout } from "./force";
import { HIT_FUNCTION, HIT_TEST, newHit, placedBunch, strandHit } from "./layers";
import { layoutInput } from "./layout-input";
import { MAX_BUNCH, ReachIndex } from "./reach";

const model = new CockpitModel();
model.ingest(runEvents());
const reach = new ReachIndex(model);
reach.sync();
const { graph, store } = model;
const PARSE_DATE = graph.byId.get("timekeeper.parse:parse_date#0") as number;
const NAIVE = graph.byId.get("tests/test_parse.py::test_naive_input") as number;

const ids = (out: Int32Array, n: number) =>
  Array.from(out.subarray(0, n), (node) => graph.ids[node] as string);

describe("ReachIndex", () => {
  it("collects a function's tests and callers, from covers and calls edges and trace samples", () => {
    const out = new Int32Array(64);
    const found = ids(out, reach.reachersOf(PARSE_DATE, store.length, -1, out, 0));
    expect(found).toHaveLength(15); // 7 tests and 8 callers
    expect(found).toContain("tests/test_parse.py::test_naive_input");
    expect(found).toContain("timekeeper.cli:main#0");
    expect(new Set(found).size).toBe(found.length);
    expect(found).not.toContain("timekeeper.parse:parse_date#0"); // never itself
  });

  it("collects what a test reaches, the same relations the other way", () => {
    const out = new Int32Array(64);
    const found = ids(out, reach.reachedBy(NAIVE, store.length, -1, out, 0));
    expect(found).toContain("timekeeper.parse:parse_date#0");
    for (const id of found)
      expect(graph.nodeTypeName(graph.byId.get(id) as number)).not.toBe("test");
  });

  it("collects a file's own functions, classes and tests", () => {
    const out = new Int32Array(64);
    const found = ids(out, reach.membersOf("timekeeper/parse.py", store.length, -1, out, 0));
    expect(found).toEqual(
      expect.arrayContaining(["timekeeper.parse:parse_date#0", "timekeeper.parse:_split_iso#0"]),
    );
    for (const id of found)
      expect(graph.files[graph.byId.get(id) as number]).toBe("timekeeper/parse.py");
  });

  it("uses only what the log said up to the event: a bunch never includes later nodes", () => {
    const sizes = [0, 400, 1_500, 1_700, store.length - 1].map((at) =>
      reach.reachersOf(PARSE_DATE, at, -1, new Int32Array(64), 0),
    );
    expect(sizes[0]).toBe(0);
    for (let k = 1; k < sizes.length; k++)
      expect(sizes[k] as number).toBeGreaterThanOrEqual(sizes[k - 1] as number);
    // A fresh index over a prefix of the log agrees with the full one at that prefix.
    const prefix = new CockpitModel();
    prefix.ingest(runEvents().slice(0, 1_700));
    const early = new ReachIndex(prefix);
    early.sync();
    const a = new Int32Array(64);
    const b = new Int32Array(64);
    const n = early.reachersOf(PARSE_DATE, 1_699, -1, a, 0);
    expect(reach.reachersOf(PARSE_DATE, 1_699, -1, b, 0)).toBe(n);
    expect(Array.from(b.subarray(0, n))).toEqual(Array.from(a.subarray(0, n)));
  });

  it("skips the node that fired and stops when the bunch is full", () => {
    const out = new Int32Array(MAX_BUNCH);
    out[0] = NAIVE;
    const n = reach.reachersOf(PARSE_DATE, store.length, NAIVE, out, 1);
    expect(n).toBe(MAX_BUNCH);
    expect(ids(out, n).filter((id) => id === "tests/test_parse.py::test_naive_input")).toHaveLength(
      1,
    );
  });

  it("lists a file's recent streaming events, newest first, up to the event", () => {
    const at = model.kinds.lastIndex("trace.sample", store.length);
    const out = new Int32Array(24);
    const n = reach.recentEvents("timekeeper/parse.py", at, out);
    expect(n).toBeGreaterThan(0);
    for (let k = 0; k < n; k++) {
      const i = out[k] as number;
      expect(i).toBeLessThanOrEqual(at);
      if (k > 0) expect(i).toBeLessThan(out[k - 1] as number);
      const event = store.event(i);
      expect(event !== null && streams(event)).toBe(true);
      if (event !== null) expect(eventFile(model, event)).toBe("timekeeper/parse.py");
    }
  });
});

/**
 * The demo run as the engine writes it: modules and changed symbols at build, no calls edges,
 * and (`tests`) each selected test's node and `covers` edges just before the first
 * `test.selected`, as the run stage emits them. Without `tests`, the engine before them.
 */
function engineLog(tests: boolean): ReproEvent[] {
  const kept = runEvents().filter(
    (e) =>
      !(e.kind === "graph.node" && e.payload.node.type === "test") &&
      !(e.kind === "graph.edge" && e.payload.type !== "imports"),
  );
  const first = kept.findIndex((e) => e.kind === "test.selected");
  const template = kept[first] as ReproEvent;
  const symbols = new Set(
    kept.flatMap((e) =>
      e.kind === "graph.node" && e.payload.node.type !== "module" ? [e.payload.node.id] : [],
    ),
  );
  const nodes: object[] = [];
  const edges: object[] = [];
  for (const e of kept) {
    if (e.kind !== "test.selected" || !tests) continue;
    const test = e.target?.test as string;
    const sep = test.indexOf("::");
    const node = { id: test, type: "test", name: test.slice(sep + 2), file: test.slice(0, sep) };
    nodes.push({
      ...template,
      kind: "graph.node",
      target: null,
      payload: { node: { ...node, line: null } },
    });
    for (const symbol of e.payload.symbols.filter((s) => symbols.has(s)))
      edges.push({
        ...template,
        kind: "graph.edge",
        target: null,
        payload: { source: test, target: symbol, type: "covers" },
      });
  }
  return renumber([
    ...kept.slice(0, first),
    ...nodes,
    ...edges,
    ...kept.slice(first),
  ]) as ReproEvent[];
}

/** The bunch the event at `i` fires in a model of `events`, landing at (1, 0). */
function bunchAt(events: readonly ReproEvent[], pick: (e: ReproEvent) => boolean) {
  const model = new CockpitModel();
  model.ingest(events);
  const reach = new ReachIndex(model);
  reach.sync();
  const positions = forceLayout(layoutInput(model.graph));
  const i = events.findLastIndex(pick);
  const hit = newHit();
  strandHit(model, events[i] as ReproEvent, hit);
  const out = new Int32Array(MAX_BUNCH);
  const n = placedBunch(reach, hit, i, out, positions, 1, 0);
  const ids = Array.from(out.subarray(0, n), (node) => model.graph.ids[node] as string);
  return {
    hit,
    ids,
    types: ids.map((id) => model.graph.nodeTypeName(model.graph.byId.get(id) as number)),
  };
}

describe("an engine log's test nodes (B14)", () => {
  const traceOfParseDate = (e: ReproEvent) =>
    e.kind === "trace.sample" && e.payload.symbol_id === "timekeeper.parse:parse_date#0";

  it("give a function's trace a bunch of several strands, from the tests that reach it", () => {
    const before = bunchAt(engineLog(false), traceOfParseDate);
    expect(before.hit.role).toBe(HIT_FUNCTION);
    expect(before.ids).toHaveLength(0); // no test nodes: nothing to fire from
    const after = bunchAt(engineLog(true), traceOfParseDate);
    expect(after.hit.role).toBe(HIT_FUNCTION);
    expect(after.ids.length).toBeGreaterThanOrEqual(4);
    expect(after.ids.length).toBeLessThanOrEqual(MAX_BUNCH);
    expect(new Set(after.types)).toEqual(new Set(["test"]));
    expect(new Set(after.ids).size).toBe(after.ids.length);
  });

  it("give a test's events a bunch: the test, then the functions it was selected for", () => {
    const finished = (e: ReproEvent) =>
      e.kind === "test.finished" && e.target?.test === "tests/test_parse.py::test_naive_input";
    const { hit, ids, types } = bunchAt(engineLog(true), finished);
    expect(hit.role).toBe(HIT_TEST);
    expect(ids[0]).toBe("tests/test_parse.py::test_naive_input");
    expect(ids).toContain("timekeeper.parse:parse_date#0");
    expect(types.slice(1).every((type) => type !== "test" && type !== "module")).toBe(true);
  });
});
