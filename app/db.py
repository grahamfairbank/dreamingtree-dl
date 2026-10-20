"""SQLite job store (data/dtdl.sqlite3).

One connection per thread (WAL mode), so the scheduler thread and request threads never share one.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Job, JobState

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    torrent_id TEXT NOT NULL,
    torrent_name TEXT NOT NULL,
    destination TEXT NOT NULL,
    state TEXT NOT NULL,
    infohash TEXT,
    album TEXT,
    library_path TEXT,
    warnings TEXT NOT NULL DEFAULT '[]',
    error_detail TEXT,
    progress REAL NOT NULL DEFAULT 0,
    created_at TEXT,
    completed_at TEXT,
    purged_at TEXT,
    last_progress_at TEXT,
    seeds INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS seen_torrents (
    torrent_id TEXT PRIMARY KEY,
    name TEXT,
    data TEXT NOT NULL,
    seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_JOB_FIELDS = {
    "torrent_id", "torrent_name", "destination", "state", "infohash", "album", "library_path",
    "warnings", "error_detail", "progress", "created_at", "completed_at", "purged_at",
    "last_progress_at", "seeds",
}
# columns added after the first schema; ALTERed into older databases on open
_MIGRATIONS = {"last_progress_at": "TEXT", "seeds": "INTEGER"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = "data/dtdl.sqlite3"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._conn().executescript(SCHEMA)
        have = {r["name"] for r in self._conn().execute("PRAGMA table_info(jobs)")}
        for col, typ in _MIGRATIONS.items():
            if col not in have:
                with self._conn() as c:
                    c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
            with self._conns_lock:
                self._conns.append(c)
        return c

    def close(self) -> None:
        with self._conns_lock:
            for c in self._conns:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._conns.clear()
        self._local = threading.local()

    # -- jobs
    @staticmethod
    def _row_to_job(r: sqlite3.Row) -> Job:
        return Job(
            id=r["id"], torrent_id=r["torrent_id"], torrent_name=r["torrent_name"],
            destination=r["destination"], state=JobState(r["state"]), infohash=r["infohash"],
            album=r["album"], library_path=r["library_path"], warnings=json.loads(r["warnings"] or "[]"),
            error_detail=r["error_detail"], progress=r["progress"] or 0.0, created_at=r["created_at"],
            completed_at=r["completed_at"], purged_at=r["purged_at"],
            last_progress_at=r["last_progress_at"], seeds=r["seeds"],
        )

    def create_job(self, torrent_id: str, torrent_name: str, destination: str) -> Job:
        c = self._conn()
        with c:
            cur = c.execute(
                "INSERT INTO jobs (torrent_id, torrent_name, destination, state, created_at)"
                " VALUES (?,?,?,?,?)",
                (torrent_id, torrent_name, destination, JobState.queued.value, now_iso()),
            )
        return self.get_job(cur.lastrowid)

    def get_job(self, job_id: int) -> Job | None:
        r = self._conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(r) if r else None

    def list_jobs(self, states: list[JobState | str] | None = None) -> list[Job]:
        c = self._conn()
        if states:
            vals = [JobState(s).value for s in states]
            q = f"SELECT * FROM jobs WHERE state IN ({','.join('?' * len(vals))}) ORDER BY id DESC"
            rows = c.execute(q, vals).fetchall()
        else:
            rows = c.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_job(self, job_id: int, **fields) -> Job:
        bad = set(fields) - _JOB_FIELDS
        if bad:
            raise ValueError(f"unknown job fields: {sorted(bad)}")
        if "state" in fields:
            fields["state"] = JobState(fields["state"]).value
        if "warnings" in fields:
            fields["warnings"] = json.dumps(list(fields["warnings"] or []))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            c = self._conn()
            with c:
                c.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    # -- events
    def add_event(self, job_id: int, level: str, message: str) -> None:
        c = self._conn()
        with c:
            c.execute("INSERT INTO events (job_id, ts, level, message) VALUES (?,?,?,?)",
                      (job_id, now_iso(), level, message))

    def list_events(self, job_id: int) -> list[dict]:
        rows = self._conn().execute(
            "SELECT job_id, ts, level, message FROM events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        return [dict(r) for r in rows]

    # -- seen_torrents (listing cache)
    def upsert_seen(self, torrent_id: str, name: str, data: dict) -> None:
        c = self._conn()
        with c:
            c.execute(
                "INSERT INTO seen_torrents (torrent_id, name, data, seen_at) VALUES (?,?,?,?)"
                " ON CONFLICT(torrent_id) DO UPDATE SET name=excluded.name, data=excluded.data,"
                " seen_at=excluded.seen_at",
                (torrent_id, name, json.dumps(data, default=str), now_iso()),
            )

    def list_seen(self) -> list[dict]:
        rows = self._conn().execute("SELECT torrent_id, name, data, seen_at FROM seen_torrents"
                                    " ORDER BY seen_at DESC, torrent_id").fetchall()
        return [{"torrent_id": r["torrent_id"], "name": r["name"], "seen_at": r["seen_at"],
                 "data": json.loads(r["data"])} for r in rows]

    # -- meta (small key/value, e.g. last purge date)
    def get_meta(self, key: str) -> str | None:
        r = self._conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        c = self._conn()
        with c:
            c.execute("INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET"
                      " value=excluded.value", (key, value))
