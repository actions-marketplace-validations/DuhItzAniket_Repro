# repro-events

The event schema of [Repro](https://github.com/DuhItzAniket/Repro), as Python tooling. Repro's
engine only emits events, and its cockpit and its Reproduction Card are projections of them, so
this schema is the contract between them. Most users install
[repro-engine](https://pypi.org/project/repro-engine/), which brings this package.

- `repro_events.schemas`: loads the JSON Schema files into a `referencing` registry, lists the
  event kinds, and builds validators for events and models.
- `repro_events.models`: Pydantic models generated from the schema.
- `repro_events.agent_schema`: draft-07, `$ref`-free variants of schemas, for an LLM's structured
  output.

```bash
repro-events validate run.jsonl    # the schema and the log's invariants: order, IDs, evidence before citation
repro-events normalise run.jsonl   # canonical JSONL, with IDs and times normalised
```

The schema itself, its examples and the rules for changing it are in
[packages/events](https://github.com/DuhItzAniket/Repro/tree/main/packages/events).

Licensed under the [Apache License 2.0](https://github.com/DuhItzAniket/Repro/blob/main/LICENSE).
