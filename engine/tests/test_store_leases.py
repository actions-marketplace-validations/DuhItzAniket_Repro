"""A running run's lease (migration 2): a run whose process is gone is ended, never left
"running" forever.

Found on 2026-10-21: the GitHub App's ``repro run`` was killed with its terminal, and its run
stayed ``running`` in the store and the fleet view. The writer that registers a run renews its
lease while the run is open, and a writer that starts ends, as ``failed`` (no card), every
running run whose lease is older than ``stale_s``. The run's events are left as they are.
"""

import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

from repro.clock import utc_timestamp
from repro.store import StoreReader
from store_support import log_line, run_record, started_writer

TIMEOUT = 30


def lease(db: Path, run_id: str) -> str | None:
    with closing(sqlite3.connect(db)) as connection:
        row = connection.execute("SELECT heartbeat_at FROM runs WHERE id = ?", (run_id,)).fetchone()
    return None if row is None else row[0]


def test_a_writer_renews_the_leases_of_the_runs_it_has_open(tmp_path: Path) -> None:
    db = tmp_path / "repro.db"
    run, done = run_record(), run_record()
    with started_writer(db, heartbeat_s=0.05) as writer:
        writer.create_run(run).result(TIMEOUT)
        writer.create_run(done).result(TIMEOUT)
        writer.finish_run(done.id, "completed", utc_timestamp(time.time())).result(TIMEOUT)
        first, finished = lease(db, run.id), lease(db, done.id)
        deadline = time.monotonic() + 10
        while lease(db, run.id) == first and time.monotonic() < deadline:
            time.sleep(0.05)
        assert first is not None
        assert lease(db, run.id) > first  # type: ignore[operator]
        assert lease(db, done.id) == finished  # a finished run's lease is not renewed


def test_a_run_whose_writer_stopped_is_ended_without_a_card(tmp_path: Path) -> None:
    db = tmp_path / "repro.db"
    run = run_record()
    with started_writer(db) as writer:
        writer.create_run(run).result(TIMEOUT)
        writer.submit(log_line(run.id)).result(TIMEOUT)
    # The process is gone; eleven minutes later a writer starts.
    later = time.time() + 660
    with started_writer(db, clock=lambda: later) as writer:
        assert writer.recovered == (run.id,)
    with StoreReader(db) as reader:
        found = reader.run(run.id)
        assert found is not None
        assert found.status == "failed"
        assert found.finished_at == utc_timestamp(later)
        assert reader.card(run.id) is None  # never a card made up for it
        assert len(list(reader.iter_events(run.id))) == 1  # its log as it was


def test_a_run_with_a_live_lease_is_left_alone(tmp_path: Path) -> None:
    db = tmp_path / "repro.db"
    run = run_record()
    with started_writer(db) as owner:
        owner.create_run(run).result(TIMEOUT)
        with started_writer(db, clock=lambda: time.time() + 60) as other:
            assert other.recovered == ()
    with StoreReader(db) as reader:
        found = reader.run(run.id)
        assert found is not None
        assert found.status == "running"


def test_a_run_from_before_leases_counts_from_its_creation(tmp_path: Path) -> None:
    db = tmp_path / "repro.db"
    with started_writer(db):
        pass
    old, recent = run_record(), run_record()
    with closing(sqlite3.connect(db)) as connection:
        for record, created in ((old, time.time() - 7200), (recent, time.time() - 30)):
            connection.execute(
                "INSERT INTO runs (id, repo, base, head, producer, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'running', ?)",
                (record.id, record.repo, record.base, record.head, record.producer,
                 utc_timestamp(created)),
            )  # fmt: skip
        connection.commit()
    with started_writer(db) as writer:
        assert writer.recovered == (old.id,)


CHILD = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from store_support import run_record, started_writer
writer = started_writer(Path(sys.argv[1]), heartbeat_s=0.05)
run = run_record()
writer.create_run(run).result(30)
print(run.id, flush=True)
time.sleep(600)
"""


def test_a_killed_process_leaves_a_run_the_next_writer_ends(tmp_path: Path) -> None:
    """The case found live: the process holding the run is killed, not stopped."""
    db = tmp_path / "repro.db"
    tests = str(Path(__file__).parent)
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(db), tests],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        run_id = child.stdout.readline().strip()
        assert run_id.startswith("run_")
    finally:
        child.kill()
        child.wait(TIMEOUT)
    with started_writer(db, clock=lambda: time.time() + 660) as writer:
        assert writer.recovered == (run_id,)
    with StoreReader(db) as reader:
        found = reader.run(run_id)
        assert found is not None
        assert found.status == "failed"
