"""Real Backend: wires the web API to Pipeline, Scheduler and the dreamingtree client.

The browse table is served from the listing cache in SQLite (seen_torrents), not by paging
the site on every filter change. A sync thread refreshes the newest pages every
SYNC_EVERY seconds and crawls the whole catalogue once on first start; a text query
also triggers a live site search whose results are added to the cache.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from app import config as config_mod
from app.backend import PAGE_SIZE, SOURCES, merge_settings, redact_settings
from app.config import Config
from app.db import Database
from app.library import LibraryIndex
from app.dreamingtree import DreamingTree
from app.models import Job, JobState, ListingRow, ShowInfo
from app.names import parse_torrent_name
from app.pipeline import Pipeline
from app.qbit import Qbit
from app.scheduler import Scheduler
from app.sources import source_suffix

log = logging.getLogger(__name__)

SYNC_EVERY = 6 * 3600
SYNC_RETRY = 15 * 60
SYNC_RECENT_PAGES = 3
FIRST_YEAR = 1989           # earliest DMB-related recordings
SITE_RESULT_CAP = 1000      # dreamingtree's search stops at 1,000 matches
MAX_PAGES_PER_SLICE = 20
LIBRARY_STATES = {JobState.copied, JobState.seeding, JobState.purged}
ACTIVE_STATES = {JobState.queued, JobState.downloading, JobState.stalled, JobState.downloaded,
                 JobState.analysing, JobState.tagged, JobState.error}


def _job_dict(job: Job) -> dict:
    d = asdict(job)
    d["state"] = job.state.value
    return d


def _row_dict(row: ListingRow) -> dict:
    """Listing cache entry, enriched with what names.py can tell from the release name."""
    try:
        tn = parse_torrent_name(row.name)
    except Exception:  # an odd name must never break the listing
        tn = None
    try:
        suffix = source_suffix(tn, ShowInfo()) if tn else ""
    except Exception:
        suffix = ""
    show_date = row.show_date or (tn.date if tn else None)
    return {
        "torrent_id": row.torrent_id,
        "name": row.name,
        "artist": row.artist or (tn.artist_code if tn else None) or "",
        "artist_code": (tn.artist_code if tn else None) or "",
        "show_date": show_date.isoformat() if isinstance(show_date, date) else "",
        "source": suffix or "taper",
        "format": (tn.fmt if tn else None) or "",
        "size_bytes": row.size_bytes,
        "seeders": row.seeders,
        "added": row.added,
    }


class LiveBackend:
    def __init__(self, cfg_path: Path | None = None, db_path: str | Path | None = None):
        self._cfg_path = cfg_path or config_mod.config_path()
        self.cfg: Config = config_mod.load(self._cfg_path)
        data_dir = self._cfg_path.parent
        self.db = Database(db_path or data_dir / "dtdl.sqlite3")
        self.dt = DreamingTree(self.cfg.dreamingtree, cookie_path=str(data_dir / "dreamingtree.cookies"))
        self.qbit = Qbit(self.cfg.qbittorrent)
        self.pipeline = Pipeline(self.cfg, self.db, self.qbit, self.dt, art_cache_dir=data_dir / "cache" / "art")
        self.scheduler = Scheduler(self.pipeline)
        self.library = LibraryIndex(Path(self.cfg.paths.library_root), data_dir / "library_index.json")
        self._login = {"ok": False, "detail": "not logged in yet"}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sync_thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        for d in (self.cfg.torrents_dir, self.cfg.work_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log.warning("cannot create %s: %s", d, e)
        try:
            self.qbit.ensure_preferences()
        except Exception as e:  # qBittorrent may still be starting; the scheduler retries jobs anyway
            log.warning("could not set qBittorrent preferences: %s", e)
        self.scheduler.start()
        self._sync_thread = threading.Thread(target=self._sync_loop, name="dtdl-sync", daemon=True)
        self._sync_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.scheduler.stop()

    # -- listing cache -------------------------------------------------
    def _cache(self, rows: list[ListingRow]) -> None:
        for r in rows:
            self.db.upsert_seen(r.torrent_id, r.name, _row_dict(r))

    def _crawl_pages(self, max_pages: int, year: int | None = None) -> tuple[int, int | None]:
        """Cache up to max_pages of one slice; returns (rows cached, site's total for the slice)."""
        n, total = 0, None
        for page in range(1, max_pages + 1):
            if self._stop.is_set():
                break
            lp = self.dt.list_page(page=page, year=year)
            total = lp.total if lp.total is not None else total
            if not lp.rows:
                break
            self._cache(lp.rows)
            n += len(lp.rows)
            if lp.pages is not None and page >= lp.pages:
                break
        return n, total

    def _crawl(self, full: bool) -> int:
        if not full:
            return self._crawl_pages(SYNC_RECENT_PAGES)[0]
        # The site caps any search at 1,000 results, so walk the catalogue one show year at a time.
        _, site_total = self._crawl_pages(1)
        n = 0
        for year in range(FIRST_YEAR, date.today().year + 2):
            if self._stop.is_set():
                break
            got, total = self._crawl_pages(MAX_PAGES_PER_SLICE, year)
            if total and total > SITE_RESULT_CAP:
                log.warning("year %s has %s torrents, over the site's %s cap: some are missed",
                            year, total, SITE_RESULT_CAP)
            n += got
        cached = len(self.db.list_seen())
        log.info("full crawl: %d rows fetched, %d cached, site reports %s", n, cached, site_total)
        if site_total and cached < site_total:
            log.warning("full crawl cached %d of %d torrents (ones without a show year are not reachable by year)",
                        cached, site_total)
        return n

    def _sync_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.library.rescan()
            except Exception as e:
                log.warning("library scan failed: %s", e)
            try:
                self._ensure_login()
                full = self.db.get_meta("full_crawl_done") != "1"
                n = self._crawl(full)
                if full and not self._stop.is_set():
                    self.db.set_meta("full_crawl_done", "1")
                self.db.set_meta("last_sync", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                log.info("listing sync: %d rows (%s)", n, "full" if full else "recent")
            except Exception as e:
                log.warning("listing sync failed: %s; retrying in %d min", e, SYNC_RETRY // 60)
                self._login = {"ok": False, "detail": f"sync failed: {e}"}
                self._stop.wait(SYNC_RETRY)
                continue
            self._stop.wait(SYNC_EVERY)

    def _ensure_login(self) -> None:
        if not self._login["ok"]:
            self.dt.login()
            self._login = {"ok": True, "detail": f"logged in as {self.cfg.dreamingtree.username}"}

    def _marks(self) -> dict[str, str]:
        marks: dict[str, str] = {}
        for job in self.db.list_jobs():
            if job.state in LIBRARY_STATES:
                marks[job.torrent_id] = "library"
            elif job.state in ACTIVE_STATES:
                marks.setdefault(job.torrent_id, "queued")
        return marks

    # -- Backend -------------------------------------------------------
    def listing(self, page: int, query: str, filters: dict[str, str]) -> dict[str, Any]:
        if query:
            try:
                self._ensure_login()
                self._cache(self.dt.list_torrents(page=1, query=query))
            except Exception as e:
                log.warning("live search failed: %s", e)
        rows = [s["data"] for s in self.db.list_seen()]
        artists = sorted({r["artist"] for r in rows if r.get("artist")})
        if query:
            q = query.lower()
            rows = [r for r in rows if q in r["name"].lower() or q in (r.get("show_date") or "")]
        if filters.get("artist"):
            rows = [r for r in rows if r.get("artist") == filters["artist"]]
        if filters.get("year"):
            rows = [r for r in rows if (r.get("show_date") or "").startswith(filters["year"])]
        if filters.get("source"):
            rows = [r for r in rows if r.get("source") == filters["source"]]
        if filters.get("library") in ("missing", "have"):
            want = filters["library"] == "have"
            rows = [r for r in rows if bool(self.library.match(
                r["name"], r.get("artist_code") or "", r.get("show_date"), r.get("source", "taper"))) == want]
        if filters.get("seeded"):  # unknown seeder counts are kept
            rows = [r for r in rows if r.get("seeders") != 0]
        rows.sort(key=lambda r: (r.get("show_date") or "", r["name"]), reverse=True)
        rows.sort(key=lambda r: r.get("seeders") == 0)  # stable: dead torrents sink to the bottom
        marks = self._marks()
        total = len(rows)
        pages = max(1, -(-total // PAGE_SIZE))
        page = min(max(1, page), pages)
        chunk = [dict(r, mark=marks.get(r["torrent_id"]),
                      library=self.library.match(r["name"], r.get("artist_code") or "", r.get("show_date"), r.get("source", "taper")))
                 for r in rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]]
        return {"rows": chunk, "page": page, "pages": pages, "total": total,
                "artists": artists, "sources": SOURCES}

    def queue(self, ids: list[str], destination: str) -> list[dict]:
        self.cfg.destination(destination)  # KeyError → 404
        names = {s["torrent_id"]: s["name"] for s in self.db.list_seen()}
        made = []
        for tid in ids:
            if tid not in names:
                raise KeyError(f"torrent {tid} is not in the listing cache")
            job = self.pipeline.queue(tid, names[tid], destination)
            threading.Thread(target=self.pipeline.advance, args=(job.id,), daemon=True).start()
            made.append(_job_dict(job))
        return made

    def jobs(self) -> list[dict]:
        return [_job_dict(j) for j in self.db.list_jobs()]

    def job_events(self, job_id: int) -> list[dict]:
        if self.db.get_job(job_id) is None:
            raise KeyError(f"no job {job_id}")
        return [{"ts": e["ts"], "message": f'[{e["level"]}] {e["message"]}' if e["level"] != "info" else e["message"]}
                for e in self.db.list_events(job_id)]

    def retry(self, job_id: int) -> dict:
        if self.db.get_job(job_id) is None:
            raise KeyError(f"no job {job_id}")
        return _job_dict(self.pipeline.retry(job_id))

    def cancel(self, job_id: int) -> dict:
        job = self.db.get_job(job_id)
        if job is None:
            raise KeyError(f"no job {job_id}")
        if job.state in (JobState.seeding, JobState.purged, JobState.cancelled):
            raise ValueError(f"cannot cancel a job in state {job.state.value}")
        return _job_dict(self.pipeline.cancel(job_id))

    def status(self) -> dict:
        try:
            qb = {"ok": True, "detail": f"qBittorrent {self.qbit.version()}"}
        except Exception as e:
            qb = {"ok": False, "detail": str(e)}
        try:
            free = shutil.disk_usage(self.cfg.staging).free
        except OSError:
            free = None
        return {"dreamingtree": dict(self._login), "qbittorrent": qb, "staging_free_bytes": free,
                "last_sync": self.db.get_meta("last_sync"),
                "library_shows": len(self.library), "library_scanned_at": self.library.scanned_at}

    def rescan_library(self) -> dict:
        n = self.library.rescan()
        return {"shows": n, "scanned_at": self.library.scanned_at}

    def get_settings(self) -> dict:
        return redact_settings(self.cfg)

    def save_settings(self, patch: dict) -> dict:
        with self._lock:
            new = merge_settings(self.cfg, patch)  # ValueError → 400
            config_mod.save(new, self._cfg_path)
            login_changed = new.dreamingtree != self.cfg.dreamingtree
            # Mutate in place so Pipeline/DreamingTree keep seeing the same objects.
            for field in Config.model_fields:
                setattr(self.cfg, field, getattr(new, field))
            self.dt.cfg = self.cfg.dreamingtree
            if login_changed:
                self._login = {"ok": False, "detail": "credentials changed; re-login needed"}
        return redact_settings(self.cfg)

    def relogin(self) -> dict:
        self._login = {"ok": False, "detail": "logging in"}
        try:
            self._ensure_login()
        except Exception as e:
            self._login = {"ok": False, "detail": f"login failed: {e}"}
        return dict(self._login)
