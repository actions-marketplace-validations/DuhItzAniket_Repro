"""The store's schema and its migrations.

Migrations are numbered from 1 and tracked by ``PRAGMA user_version``. Each one runs in its own
``BEGIN IMMEDIATE`` transaction together with the version bump, so a crash leaves the database
at a whole version. A database whose version is newer than this code knows is refused: opening
it could misread or damage data written by a later Repro.

The schema enforces the event log's rules itself (ARCHITECTURE §5.3), so no code path can break
them:
- events and evidence are append-only: triggers abort any update or delete;
- each run's ``seq`` starts at 1 and has no gaps: a trigger rejects any insert that is not the
  run's next number, and ``PRIMARY KEY (run_id, seq)`` rejects duplicates;
- evidence belongs to the ``evidence.created`` event that introduced it.
"""

import sqlite3
from typing import Final

#: Run lifecycle in the registry: ``running``, then the ``run.finished`` outcome. Migration 1
#: spells these out (a shipped migration never changes); a test keeps the two in step.
RUN_STATES: Final = ("running", "completed", "failed", "cancelled")

_MIGRATION_1 = """
CREATE TABLE runs (
    id          TEXT PRIMARY KEY CHECK (id GLOB 'run_*'),
    repo        TEXT NOT NULL,
    base        TEXT NOT NULL,
    head        TEXT NOT NULL,
    producer    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    CHECK ((status = 'running') = (finished_at IS NULL))
) STRICT;

CREATE TABLE events (
    run_id  TEXT NOT NULL REFERENCES runs (id),
    seq     INTEGER NOT NULL CHECK (seq >= 1),
    id      TEXT NOT NULL UNIQUE CHECK (id GLOB 'evt_*'),
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    stage   TEXT NOT NULL,
    status  TEXT NOT NULL,
    body    TEXT NOT NULL CHECK (json_valid(body)),
    PRIMARY KEY (run_id, seq)
) STRICT, WITHOUT ROWID;

CREATE TRIGGER events_seq_is_next BEFORE INSERT ON events
WHEN NEW.seq != COALESCE((SELECT MAX(seq) FROM events WHERE run_id = NEW.run_id), 0) + 1
BEGIN
    SELECT RAISE(ABORT, 'events: seq must be the run''s next number');
END;

CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TABLE artifacts (
    run_id      TEXT NOT NULL REFERENCES runs (id),
    sha256      TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    path        TEXT NOT NULL,
    media_type  TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, sha256, path)
) STRICT, WITHOUT ROWID;

CREATE UNIQUE INDEX artifacts_path ON artifacts (run_id, path);

CREATE TABLE evidence (
    id      TEXT PRIMARY KEY CHECK (id GLOB 'ev_*'),
    run_id  TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    type    TEXT NOT NULL,
    body    TEXT NOT NULL CHECK (json_valid(body)),
    FOREIGN KEY (run_id, seq) REFERENCES events (run_id, seq)
) STRICT;

CREATE UNIQUE INDEX evidence_event ON evidence (run_id, seq);

CREATE TRIGGER evidence_needs_its_event BEFORE INSERT ON evidence
WHEN (SELECT kind FROM events WHERE run_id = NEW.run_id AND seq = NEW.seq)
     IS NOT 'evidence.created'
BEGIN
    SELECT RAISE(ABORT, 'evidence must belong to an evidence.created event');
END;

CREATE TRIGGER evidence_no_update BEFORE UPDATE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence is append-only');
END;

CREATE TRIGGER evidence_no_delete BEFORE DELETE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence is append-only');
END;

CREATE TABLE cards (
    run_id      TEXT PRIMARY KEY REFERENCES runs (id),
    status      TEXT NOT NULL CHECK (status IN ('VERIFIED', 'FLAGGED', 'UNVERIFIABLE')),
    body        TEXT NOT NULL CHECK (json_valid(body)),
    created_at  TEXT NOT NULL
) STRICT;
"""

#: Migration 2: a running run's lease. The writer that registered a run renews ``heartbeat_at``
#: while the run is open; a run whose heartbeat stopped long ago lost its process (killed, its
#: terminal closed, the machine stopped) and is ended as ``failed`` when a writer next starts
#: (``StoreWriter``). Runs from before this migration have none.
_MIGRATION_2 = """
ALTER TABLE runs ADD COLUMN heartbeat_at TEXT;
"""

#: Every migration, in order; migration N is ``MIGRATIONS[N - 1]``. Never edit a shipped one.
MIGRATIONS: Final[tuple[str, ...]] = (_MIGRATION_1, _MIGRATION_2)
SCHEMA_VERSION: Final = len(MIGRATIONS)


class SchemaError(RuntimeError):
    """The database's schema cannot be used by this version of Repro."""


def user_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def migrate(connection: sqlite3.Connection) -> int:
    """Brings the database to ``SCHEMA_VERSION``; returns how many migrations ran."""
    current = user_version(connection)
    if current > SCHEMA_VERSION:
        raise SchemaError(
            f"the database is at schema version {current}, newer than this Repro knows "
            f"({SCHEMA_VERSION}): upgrade Repro, or use another data directory"
        )
    applied = 0
    for version in range(current + 1, SCHEMA_VERSION + 1):
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Re-read under the write lock: another process may have migrated meanwhile.
            if user_version(connection) >= version:
                connection.execute("COMMIT")
                continue
            for statement in _statements(MIGRATIONS[version - 1]):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        applied += 1
    return applied


def _statements(script: str) -> list[str]:
    """Splits a migration into whole statements (triggers contain semicolons)."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise SchemaError(f"incomplete statement in a migration: {buffer.strip()[:60]}")
    return statements
