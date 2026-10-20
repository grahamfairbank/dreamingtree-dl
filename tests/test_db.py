import threading

from app.db import Database
from app.models import JobState


def test_job_crud(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    j = db.create_job("12", "dmb2020-01-01.flac16", "Dave Matthews Band")
    assert j.state == JobState.queued and j.created_at and j.warnings == []
    j = db.update_job(j.id, state=JobState.error, warnings=["a", "b"], progress=0.5, error_detail="x")
    assert j.state == JobState.error and j.warnings == ["a", "b"] and j.progress == 0.5
    db.create_job("13", "n", "d")
    assert [x.id for x in db.list_jobs([JobState.error])] == [j.id]
    assert len(db.list_jobs()) == 2
    assert db.get_job(999) is None


def test_events_seen_meta(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    j = db.create_job("1", "n", "d")
    db.add_event(j.id, "info", "one")
    db.add_event(j.id, "error", "two")
    assert [(e["level"], e["message"]) for e in db.list_events(j.id)] == [("info", "one"), ("error", "two")]
    db.upsert_seen("5", "a", {"size": 1})
    db.upsert_seen("5", "b", {"size": 2})
    assert db.list_seen()[0]["data"] == {"size": 2} and len(db.list_seen()) == 1
    assert db.get_meta("k") is None
    db.set_meta("k", "v")
    assert db.get_meta("k") == "v"


def test_threads_and_wal(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    j = db.create_job("1", "n", "d")
    errs = []

    def work(i):
        try:
            for k in range(20):
                db.update_job(j.id, progress=k / 20)
                db.add_event(j.id, "info", f"{i}-{k}")
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(db.list_events(j.id)) == 80
    assert db._conn().execute("PRAGMA journal_mode").fetchone()[0] == "wal"
