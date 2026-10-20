import time
from datetime import datetime, timezone

from app.db import Database
from app.scheduler import Scheduler


class FakePipeline:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "s.sqlite3")
        self.ticks = 0
        self.purges = 0

    def tick(self):
        self.ticks += 1
        if self.ticks == 1:
            raise RuntimeError("boom")        # a failing tick must not kill the loop
        return []

    def purge_expired(self, now=None):
        self.purges += 1
        return []


def test_purge_once_per_day(tmp_path):
    p = FakePipeline(tmp_path)
    s = Scheduler(p)
    d1 = datetime(2026, 10, 20, 3, tzinfo=timezone.utc)
    s.purge_if_due(d1)
    s.purge_if_due(d1.replace(hour=9))
    assert p.purges == 1
    s.purge_if_due(datetime(2026, 10, 21, 3, tzinfo=timezone.utc))
    assert p.purges == 2


def test_run_once_checks_purge_hourly(tmp_path):
    p = FakePipeline(tmp_path)
    s = Scheduler(p)
    s.run_once()          # tick raises, purge check still runs
    s.run_once()          # within the hour: tick only
    assert p.ticks == 2 and p.purges == 1


def test_start_stop_thread(tmp_path):
    p = FakePipeline(tmp_path)
    s = Scheduler(p, tick_seconds=0.01)
    s.start()
    s.start()             # idempotent
    deadline = time.time() + 3
    while p.ticks < 3 and time.time() < deadline:
        time.sleep(0.01)
    s.stop()
    n = p.ticks
    assert n >= 3
    time.sleep(0.1)
    assert p.ticks == n
