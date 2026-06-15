"""Schema smoke test — locks the invariants the design relies on (§9, §16, §20).

The database, not the agent, enforces dedup and exactly-one-event. If a future
edit drops a UNIQUE constraint, a CHECK, or a foreign key, these fail.
"""

import pathlib
import sqlite3

import pytest

SCHEMA = pathlib.Path(__file__).resolve().parent.parent / "schema.sql"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA.read_text())
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


def _add_source(conn, key="github.com/anthropics/skills"):
    conn.execute(
        "INSERT INTO sources (canonical_key, canonical_url, platform) VALUES (?,?,?) "
        "ON CONFLICT(canonical_key) DO UPDATE SET last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')",
        (key, f"https://{key}", "github"),
    )
    return conn.execute("SELECT id FROM sources WHERE canonical_key=?", (key,)).fetchone()[0]


def test_schema_applies_all_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "sources", "source_aliases", "source_relationships", "source_observations",
        "skill_candidates", "scans", "search_queries", "notifications",
        "manual_overrides", "schema_migrations",
    } <= names


def test_baseline_migration_recorded(conn):
    assert conn.execute("SELECT version FROM schema_migrations").fetchone()[0] == "0001"


def test_source_upsert_is_idempotent(conn):
    _add_source(conn)
    _add_source(conn)
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1


def test_duplicate_canonical_key_rejected(conn):
    _add_source(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sources (canonical_key,canonical_url,platform) "
            "VALUES ('github.com/anthropics/skills','x','github')"
        )


def test_duplicate_alias_rejected(conn):
    sid = _add_source(conn)
    conn.execute("INSERT INTO source_aliases (source_id,normalized_alias) VALUES (?,?)", (sid, "a"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source_aliases (source_id,normalized_alias) VALUES (?,?)", (sid, "a"))


def test_event_dedupe_on_conflict_is_noop(conn):
    sid = _add_source(conn)
    for _ in range(2):
        conn.execute(
            "INSERT INTO notifications (dedupe_key,event_type,source_id,payload_json) "
            "VALUES ('accepted:1','new_source.accepted',?,'{}') ON CONFLICT(dedupe_key) DO NOTHING",
            (sid,),
        )
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1


def test_duplicate_dedupe_key_rejected(conn):
    sid = _add_source(conn)
    conn.execute(
        "INSERT INTO notifications (dedupe_key,event_type,source_id,payload_json) "
        "VALUES ('accepted:1','new_source.accepted',?,'{}')", (sid,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO notifications (dedupe_key,event_type,source_id,payload_json) "
            "VALUES ('accepted:1','new_source.accepted',?,'{}')", (sid,),
        )


def test_foreign_key_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO source_aliases (source_id,normalized_alias) VALUES (999,'orphan')")


def test_status_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sources (canonical_key,canonical_url,platform,status) "
            "VALUES ('x','x','github','bogus')"
        )


def test_source_type_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sources (canonical_key,canonical_url,platform,source_type) "
            "VALUES ('x','x','github','nonsense')"
        )


def test_relationship_type_check(conn):
    sid = _add_source(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO source_relationships (source_id,target_url,type) VALUES (?,?,?)",
            (sid, "https://x", "bogus_rel"),
        )


def test_pending_events_view_tracks_outbox(conn):
    sid = _add_source(conn)
    conn.execute(
        "INSERT INTO notifications (dedupe_key,event_type,source_id,payload_json) "
        "VALUES ('accepted:1','new_source.accepted',?,'{}')", (sid,),
    )
    assert conn.execute("SELECT COUNT(*) FROM v_pending_events").fetchone()[0] == 1
    conn.execute("UPDATE notifications SET delivered_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    assert conn.execute("SELECT COUNT(*) FROM v_pending_events").fetchone()[0] == 0


def test_evidence_is_idempotent_per_signal(conn):
    sid = _add_source(conn)
    for w in (2.0, 3.0):
        conn.execute(
            "INSERT INTO source_observations (source_id,evidence_type,weight) VALUES (?,?,?) "
            "ON CONFLICT(source_id,evidence_type) DO UPDATE SET weight=excluded.weight",
            (sid, "has_skill_md", w),
        )
    assert conn.execute(
        "SELECT COUNT(*), MAX(weight) FROM source_observations WHERE source_id=?", (sid,)
    ).fetchone() == (1, 3.0)
