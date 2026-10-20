"""Backend contract between the web server and the pipeline.

`Backend` lists every operation the server needs. `FakeBackend` serves in-memory demo data
(DTDL_FAKE=1). The integrator writes a real implementation wrapping Pipeline + DreamingTree.

Shapes (all JSON-able dicts):
  listing row: torrent_id, name, artist, show_date (ISO), source, format, size_bytes, seeders,
               added, mark ('queued' | 'library' | None)
  job:         dataclass app.models.Job fields as a dict (state is the string value)
  event:       {"ts": str, "message": str}
  status:      {"dreamingtree": {"ok", "detail"}, "qbittorrent": {"ok", "detail"},
                "staging_free_bytes": int | None}
  settings:    config.Config.model_dump() with dreamingtree.password removed and
               dreamingtree.password_set added
"""
from __future__ import annotations

import copy
import random
import threading
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from app.config import Config, Destination
from app.models import Job, JobState

PAGE_SIZE = 25
SOURCES = ["taper", "sbd", "siriusxm", "fm", "webcast", "matrix", "tv"]


class Backend(Protocol):
    def listing(self, page: int, query: str, filters: dict[str, str]) -> dict[str, Any]:
        """Return {rows, page, pages, total, artists}. filters keys: artist, year, source, library, seeded.

        filters["seeded"] (any value) hides rows with 0 seeders; with it off they sort last.

        Each row also carries `library`: None or {level: exact|same|other, paths, sources}.
        filters["library"] is "have" or "missing"."""

    def queue(self, ids: list[str], destination: str) -> list[dict]:
        """Create jobs for torrent ids; returns the new jobs. KeyError on unknown destination."""

    def jobs(self) -> list[dict]: ...

    def job_events(self, job_id: int) -> list[dict]:
        """KeyError if the job does not exist."""

    def retry(self, job_id: int) -> dict: ...

    def cancel(self, job_id: int) -> dict: ...

    def status(self) -> dict: ...

    def get_settings(self) -> dict:
        """Password redacted: never include the stored password."""

    def save_settings(self, patch: dict) -> dict:
        """Merge patch into config and save. Empty/missing dreamingtree.password keeps the old one."""

    def relogin(self) -> dict:
        """Force a dreamingtree login; returns {"ok": bool, "detail": str}."""

    def rescan_library(self) -> dict:
        """Re-read the music library; returns {"shows": int, "scanned_at": str}."""


def redact_settings(cfg: Config) -> dict:
    data = cfg.model_dump()
    pw = data["dreamingtree"].pop("password", "")
    data["dreamingtree"]["password_set"] = bool(pw)
    data["qbittorrent"].pop("password", None)
    return data


def merge_settings(cfg: Config, patch: dict) -> Config:
    """Apply a settings patch; a blank password never overwrites the stored one."""
    data = cfg.model_dump()
    patch = copy.deepcopy(patch)
    dt = patch.get("dreamingtree", {})
    dt.pop("password_set", None)
    if not dt.get("password"):
        dt.pop("password", None)
    patch.pop("qbittorrent", None)  # not editable from the UI
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return Config.model_validate(data)


_SHOWS = [
    ("dmb", "Dave Matthews Band", "dmb2025-07-12.ca-11.flac16", "taper"),
    ("dmb", "Dave Matthews Band", "dmb2025-07-11.dpa4011.flac24", "taper"),
    ("dmb", "Dave Matthews Band", "dmb2024-05-24.siriusxm.flac16", "siriusxm"),
    ("dmb", "Dave Matthews Band", "dmb2018-07-10.akgc414xlii.flac16", "taper"),
    ("dmb", "Dave Matthews Band", "dmb1994-10-27.sbd.shn", "sbd"),
    ("dmb", "Dave Matthews Band", "dmb1998-08-15.fm.flac16", "fm"),
    ("dmb", "Dave Matthews Band", "dmb2021-09-04.webcast.flac16", "webcast"),
    ("dmb", "Dave Matthews Band", "dmb2003-06-21.matrix.flac16", "matrix"),
    ("d&t", "Dave Matthews & Tim Reynolds", "d&t2025-01-24.siriusxm.flac16", "siriusxm"),
    ("d&t", "Dave Matthews & Tim Reynolds", "d&t2007-03-09.ck63.flac16", "taper"),
    ("d&t", "Dave Matthews & Tim Reynolds", "d&t1999-04-17.sbd.shn", "sbd"),
    ("dm", "Dave Matthews", "dm2012-11-03.dpa4021.flac16", "taper"),
]


def _sample_listing() -> list[dict]:
    rng = random.Random(7)
    rows: list[dict] = []
    for i in range(70):
        code, artist, tmpl, source = _SHOWS[i % len(_SHOWS)]
        d = date(1993, 1, 1) + timedelta(days=rng.randint(0, 32 * 365))
        tail = tmpl.split(".", 1)[1]
        fmt = tail.rsplit(".", 1)[-1]
        name = f"{code}{d.isoformat()}.{tail}"
        if i < len(_SHOWS):
            name = tmpl
            d = date.fromisoformat(tmpl[len(code):len(code) + 10])
        rows.append({
            "torrent_id": str(1000 + i), "name": name, "artist": artist,
            "show_date": d.isoformat(), "source": source, "format": fmt,
            "size_bytes": rng.randint(300, 2400) * 1024 * 1024,
            "seeders": rng.randint(0, 40),
            "added": (datetime(2026, 10, 19) - timedelta(days=rng.randint(0, 900))).strftime("%Y-%m-%d"),
            "mark": None,
            "library": ({"level": "exact", "paths": [f"DMB_Live/PrevYear/cy{d.year}/{name}"], "sources": [source]}
                        if i % 5 == 1 else
                        {"level": "same", "paths": [f"DMB_Live/PrevYear/cy{d.year}/{code}{d.isoformat()}.other.flac16"], "sources": [source]}
                        if i % 9 == 4 else
                        {"level": "other", "paths": [f"DMB_Live/PrevYear/cy{d.year}/{code}{d.isoformat()}.ck63.flac16"], "sources": ["taper"]}
                        if i % 7 == 2 and source != "taper" else None),
        })
    rows.sort(key=lambda r: r["added"], reverse=True)
    return rows


class FakeBackend:
    """In-memory demo backend. Jobs advance on every jobs() call so the UI shows movement."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows = _sample_listing()
        self._cfg = Config(destinations=[
            Destination(label="Dave Matthews Band", path="/srv/music/"),
            Destination(label="Test library", path="/srv/music/Test/"),
        ])
        self._cfg.dreamingtree.username = "demo"
        self._cfg.dreamingtree.password = "demo-secret-do-not-leak"
        self._jobs: dict[int, Job] = {}
        self._events: dict[int, list[dict]] = {}
        self._next_id = 1
        self._login_ok = True
        self._seed_jobs()

    # -- helpers -------------------------------------------------------
    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _event(self, job_id: int, message: str) -> None:
        self._events.setdefault(job_id, []).append({"ts": self._now(), "message": message})

    def _add_job(self, row: dict, destination: str, state: JobState = JobState.queued, **kw: Any) -> Job:
        job = Job(id=self._next_id, torrent_id=row["torrent_id"], torrent_name=row["name"],
                  destination=destination, state=state, created_at=self._now(), **kw)
        self._jobs[job.id] = job
        self._next_id += 1
        self._event(job.id, f"queued for {destination}")
        row["mark"] = "queued"
        return job

    def _seed_jobs(self) -> None:
        by = {x["name"]: x for x in self._rows}
        r = [by["dmb2025-07-12.ca-11.flac16"], by["dmb2025-07-11.dpa4011.flac24"], by["dmb2024-05-24.siriusxm.flac16"]]
        j = self._add_job(r[0], "Dave Matthews Band", JobState.downloading, progress=0.42,
                          infohash="a" * 40)
        self._event(j.id, "torrent added to qBittorrent")
        j = self._add_job(r[1], "Dave Matthews Band", JobState.seeding, progress=1.0,
                          album="2025-07-11: Mountain View, CA",
                          library_path="/srv/music/DMB_Live/" + r[1]["name"],
                          warnings=["setlist has 22 tracks but 21 files; used filename titles"],
                          completed_at=self._now())
        for m in ("download complete", "tagged 21 files", "copied to library"):
            self._event(j.id, m)
        r[1]["mark"] = "library"
        j = self._add_job(r[2], "Dave Matthews Band", JobState.error,
                          error_detail="info file not found in torrent folder")
        self._event(j.id, "error: info file not found in torrent folder")
        dead = next(x for x in self._rows if not x["mark"] and x["seeders"] == 0) if any(
            x["seeders"] == 0 for x in self._rows) else self._rows[-1]
        j = self._add_job(dead, "Dave Matthews Band", JobState.stalled, progress=0.0, infohash="b" * 40,
                          seeds=0, last_progress_at=(datetime.now() - timedelta(hours=31)).isoformat(timespec="seconds"))
        self._event(j.id, "stalled: no progress for 24h at 0% (seeds: 0)")

    def _tick(self) -> None:
        for job in self._jobs.values():
            if job.state is JobState.queued:
                job.state = JobState.downloading
            elif job.state is JobState.downloading:
                job.progress = min(1.0, job.progress + 0.12)
                if job.progress >= 1.0:
                    job.state = JobState.seeding
                    job.album = "2025-07-12: Mountain View, CA"
                    job.library_path = f"/srv/music/DMB_Live/{job.torrent_name}"
                    job.completed_at = self._now()
                    self._event(job.id, "copied to library")

    @staticmethod
    def _job_dict(job: Job) -> dict:
        d = asdict(job)
        d["state"] = job.state.value
        return d

    # -- Backend -------------------------------------------------------
    def listing(self, page: int, query: str, filters: dict[str, str]) -> dict[str, Any]:
        with self._lock:
            rows = list(self._rows)
            artists = sorted({r["artist"] for r in rows})
        if query:
            q = query.lower()
            rows = [r for r in rows if q in r["name"].lower() or q in r["show_date"]]
        if filters.get("artist"):
            rows = [r for r in rows if r["artist"] == filters["artist"]]
        if filters.get("year"):
            rows = [r for r in rows if r["show_date"].startswith(filters["year"])]
        if filters.get("source"):
            rows = [r for r in rows if r["source"] == filters["source"]]
        if filters.get("seeded"):
            rows = [r for r in rows if r.get("seeders") != 0]
        rows.sort(key=lambda r: r.get("seeders") == 0)
        if filters.get("library") in ("have", "missing"):
            want = filters["library"] == "have"
            rows = [r for r in rows if bool(r.get("library")) == want]
        total = len(rows)
        pages = max(1, -(-total // PAGE_SIZE))
        page = min(max(1, page), pages)
        chunk = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        return {"rows": copy.deepcopy(chunk), "page": page, "pages": pages, "total": total,
                "artists": artists, "sources": SOURCES}

    def queue(self, ids: list[str], destination: str) -> list[dict]:
        with self._lock:
            self._cfg.destination(destination)  # KeyError if unknown
            made = []
            for row in self._rows:
                if row["torrent_id"] in ids and not row["mark"]:
                    made.append(self._job_dict(self._add_job(row, destination)))
            return made

    def jobs(self) -> list[dict]:
        with self._lock:
            self._tick()
            return [self._job_dict(j) for j in sorted(self._jobs.values(), key=lambda j: -j.id)]

    def _get(self, job_id: int) -> Job:
        if job_id not in self._jobs:
            raise KeyError(f"no job {job_id}")
        return self._jobs[job_id]

    def job_events(self, job_id: int) -> list[dict]:
        with self._lock:
            self._get(job_id)
            return list(self._events.get(job_id, []))

    def retry(self, job_id: int) -> dict:
        with self._lock:
            job = self._get(job_id)
            if job.state not in (JobState.error, JobState.abandoned):
                raise ValueError("only error or abandoned jobs can be retried")
            job.state, job.error_detail, job.progress = JobState.queued, None, 0.0
            self._event(job_id, "retry requested")
            return self._job_dict(job)

    def cancel(self, job_id: int) -> dict:
        with self._lock:
            job = self._get(job_id)
            if job.state in (JobState.seeding, JobState.purged, JobState.cancelled):
                raise ValueError(f"cannot cancel a job in state {job.state.value}")
            job.state = JobState.cancelled
            self._event(job_id, "cancelled by user")
            return self._job_dict(job)

    def status(self) -> dict:
        return {
            "dreamingtree": {"ok": self._login_ok, "detail": "logged in as demo" if self._login_ok else "login failed"},
            "qbittorrent": {"ok": True, "detail": "qBittorrent 5.2.4 (demo)"},
            "staging_free_bytes": 1_840_000_000_000,
            "library_shows": 670, "library_scanned_at": "2026-10-20T06:00:00Z",
        }

    def get_settings(self) -> dict:
        return redact_settings(self._cfg)

    def save_settings(self, patch: dict) -> dict:
        with self._lock:
            self._cfg = merge_settings(self._cfg, patch)
        return redact_settings(self._cfg)

    def rescan_library(self) -> dict:
        return {"shows": 670, "scanned_at": self._now()}

    def relogin(self) -> dict:
        self._login_ok = True
        return {"ok": True, "detail": "logged in as demo"}
