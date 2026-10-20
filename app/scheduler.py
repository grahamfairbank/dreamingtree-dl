"""Background loop: poll qBittorrent, advance jobs, daily retention purge.

One thread only: it ticks every `tick_seconds` (poll + advance), and every `purge_check_seconds`
checks whether the retention purge is due (it acts at most once per calendar day, remembered in the DB).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from .pipeline import Pipeline

log = logging.getLogger("dtdl.scheduler")
LAST_PURGE_KEY = "last_purge_date"


class Scheduler:
    def __init__(self, pipeline: Pipeline, tick_seconds: float = 30.0, purge_check_seconds: float = 3600.0):
        self.pipeline = pipeline
        self.tick_seconds = tick_seconds
        self.purge_check_seconds = purge_check_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_purge_check = float("-inf")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dtdl-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.tick_seconds)

    def run_once(self, now: datetime | None = None) -> None:
        """One iteration: tick, then the purge check if an hour has passed. Never raises."""
        try:
            self.pipeline.tick()
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        mono = time.monotonic()
        if mono - self._last_purge_check >= self.purge_check_seconds:
            self._last_purge_check = mono
            self.purge_if_due(now)

    def purge_if_due(self, now: datetime | None = None) -> list[int]:
        now = now or datetime.now(timezone.utc)
        today = now.date().isoformat()
        try:
            if self.pipeline.db.get_meta(LAST_PURGE_KEY) == today:
                return []
            purged = self.pipeline.purge_expired(now)
            self.pipeline.db.set_meta(LAST_PURGE_KEY, today)
            return purged
        except Exception:  # noqa: BLE001
            log.exception("purge check failed")
            return []
