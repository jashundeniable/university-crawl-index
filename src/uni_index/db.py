"""
The SQL "master index": dedupes universities by domain and tracks crawl status
over time. Built on stdlib `sqlite3` on purpose — zero extra dependency, one file,
nothing to install. See the README for the note on moving to a hosted Postgres
database later without changing how the rest of the codebase calls this module.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS universities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain          TEXT NOT NULL UNIQUE,          -- the dedupe key
    country         TEXT NOT NULL,
    type            TEXT NOT NULL DEFAULT 'other',
    subdomains      TEXT NOT NULL DEFAULT '[]',    -- JSON-encoded list
    priority        INTEGER NOT NULL DEFAULT 3,
    source_hash     TEXT NOT NULL,                 -- fingerprint of the canonical record
    crawl_status    TEXT NOT NULL DEFAULT 'pending',-- pending | in_progress | done | failed
    last_crawled_at TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_universities_status ON universities(crawl_status);
"""

VALID_STATUSES = {"pending", "in_progress", "done", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def sync_records(conn: sqlite3.Connection, canonical_records: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Upsert canonical records into the master index.

      - new domain             -> inserted, crawl_status = 'pending'
      - existing, hash changed -> fields updated, crawl_status reset to 'pending'
                                   (new/changed info may mean new subdomains to crawl)
      - existing, hash same    -> left completely untouched (including crawl_status)
    """
    stats = {"inserted": 0, "updated": 0, "unchanged": 0}
    existing = {
        row["domain"]: row
        for row in conn.execute("SELECT domain, source_hash FROM universities").fetchall()
    }

    for rec in canonical_records:
        domain = rec["domain"]
        now = _now()

        if domain not in existing:
            conn.execute(
                """INSERT INTO universities
                   (domain, country, type, subdomains, priority, source_hash,
                    crawl_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (domain, rec["country"], rec["type"], json.dumps(rec["subdomains"]),
                 rec["priority"], rec["source_hash"], now, now),
            )
            stats["inserted"] += 1

        elif existing[domain]["source_hash"] != rec["source_hash"]:
            conn.execute(
                """UPDATE universities
                   SET country=?, type=?, subdomains=?, priority=?, source_hash=?,
                       crawl_status='pending', updated_at=?
                   WHERE domain=?""",
                (rec["country"], rec["type"], json.dumps(rec["subdomains"]),
                 rec["priority"], rec["source_hash"], now, domain),
            )
            stats["updated"] += 1

        else:
            stats["unchanged"] += 1

    return stats


def status_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    rows = conn.execute(
        "SELECT crawl_status, COUNT(*) AS n FROM universities GROUP BY crawl_status ORDER BY n DESC"
    ).fetchall()
    return {row["crawl_status"]: row["n"] for row in rows}


def total_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM universities").fetchone()["n"]


def mark_status(
    conn: sqlite3.Connection, domain: str, crawl_status: str, error: Optional[str] = None
) -> bool:
    """Used by your crawler to report back status for a domain. Returns False if domain is unknown."""
    if crawl_status not in VALID_STATUSES:
        raise ValueError(f"crawl_status must be one of {sorted(VALID_STATUSES)}")

    now = _now()
    cur = conn.execute(
        """UPDATE universities
           SET crawl_status=?, last_error=?, updated_at=?,
               last_crawled_at = CASE WHEN ? IN ('done','failed') THEN ? ELSE last_crawled_at END
           WHERE domain=?""",
        (crawl_status, error, now, crawl_status, now, domain),
    )
    return cur.rowcount > 0
