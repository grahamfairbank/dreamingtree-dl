from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Config, Destination, PathsCfg
from app.db import Database
from app.models import JobState, ShowInfo, Track, TorrentName
from app.pipeline import Hooks, Pipeline

NAME = "dmb2020-02-03.ca-11.flac16"


class FakeDT:
    def __init__(self):
        self.downloads = 0

    def login(self):
        pass

    def download_torrent(self, torrent_id):
        self.downloads += 1
        return b"torrent-" + torrent_id.encode()


class FakeQbit:
    def __init__(self, content: Path):
        self.content = content
        self.progress = 0.0
        self.adds = 0
        self.deleted = []
        self.state = "downloading"

    def add(self, torrent, save_path, tag):
        self.adds += 1
        self.save_path = save_path
        return "ab" * 20

    def info(self, h):
        return {"hash": h, "state": self.state, "progress": self.progress,
                "content_path": str(self.content), "save_path": str(self.content.parent),
                "completion_on": 1_700_000_000 if self.progress >= 1 else -1}

    def delete(self, h, delete_files):
        self.deleted.append((h, delete_files))


class Env:
    pass


def make_hooks(env):
    def parse_info(text):
        env.parsed.append(text)
        return ShowInfo(artist="Dave Matthews Band", city="Chicago", region="IL", date=date(2020, 2, 3),
                        tracks=[Track(i, None, f"Song {i}") for i in (1, 2)])

    def tag_show(files, info, album, artist, cover):
        env.tagged.append((list(files), album, artist, cover))
        for f, t in zip(files, info.tracks):
            env.titles[f] = (None, t.number, t.title)
        return ["tag warning"]

    def to_flac(src, dest):
        out = dest / (src.stem + ".flac")
        out.write_bytes(b"converted:" + src.read_bytes())
        env.converted.append(src.name)
        return out

    return Hooks(verify_flac=lambda p: None,  # fixtures are fake bytes, not real FLAC
        parse_torrent_name=lambda raw: TorrentName(raw, "dmb", date(2020, 2, 3), ["ca-11"], "flac16"),
        parse_info_file=parse_info,
        source_suffix=lambda n, i: "",
        album_name=lambda d, c, r, co, s, fallback=None: f"{d.isoformat()}: {c}, {r}{' ' + s if s else ''}",
        find_cover=lambda d, dt, code, cache=None, **kw: env.cover,
        tag_show=tag_show, to_flac=to_flac,
        read_tags=lambda p: env.titles[p],
    )


@pytest.fixture
def env(tmp_path):
    e = Env()
    e.parsed, e.tagged, e.converted, e.titles, e.cover = [], [], [], {}, b"JPEG"
    staging, lib = tmp_path / "staging", tmp_path / "lib"
    e.content = staging / "torrents" / NAME
    e.content.mkdir(parents=True)
    (e.content / "01.flac").write_bytes(b"a")
    (e.content / "02.shn").write_bytes(b"b")
    (e.content / "info.txt").write_text("Dave Matthews Band\nInfo text that is big " * 5)
    (e.content / "ffp.txt").write_text("x" * 5000)
    (e.content / "md5.txt").write_text("x" * 5000)
    e.cfg = Config(
        paths=PathsCfg(staging=str(staging), library_root=str(lib)),
        library_mounts={"/video/music/": str(lib) + "/"},
        destinations=[Destination(label="DMB", path="/video/music/",
                                  folder_template={"dmb": "DMB_Live/{year}/{torrent_name}", "default": "Other/{torrent_name}"})],
        retention_days=90,
    )
    e.lib = lib
    e.db = Database(tmp_path / "t.sqlite3")
    e.qbit = FakeQbit(e.content)
    e.dt = FakeDT()
    e.pl = Pipeline(e.cfg, e.db, e.qbit, e.dt, make_hooks(e))
    e.target = lib / "DMB_Live" / "2020" / NAME
    return e


def state(e, job):
    return e.db.get_job(job.id).state


def run_to_seeding(e):
    job = e.pl.queue("7", NAME, "DMB")
    e.pl.advance(job.id)
    assert state(e, job) == JobState.downloading
    e.qbit.progress = 1.0
    e.pl.advance(job.id)
    return job


def test_happy_path(env):
    e = env
    job = e.pl.queue("7", NAME, "DMB")
    assert state(e, job) == JobState.queued
    e.pl.advance(job.id)
    j = e.db.get_job(job.id)
    assert j.state == JobState.downloading and j.infohash == "ab" * 20
    assert e.qbit.save_path == str(e.cfg.torrents_dir)
    e.qbit.progress = 0.4
    e.pl.advance(job.id)
    assert e.db.get_job(job.id).progress == 0.4 and state(e, job) == JobState.downloading
    e.qbit.progress = 1.0
    e.pl.advance(job.id)
    j = e.db.get_job(job.id)
    assert j.state == JobState.seeding
    assert j.completed_at.startswith("2023-11-14")
    assert j.album == "2020-02-03: Chicago, IL"
    assert j.library_path == str(e.target)
    assert sorted(p.name for p in e.target.iterdir()) == ["01 Song 1.flac", "02 Song 2.flac", "cover.jpg", "info.txt"]
    assert (e.target / "02 Song 2.flac").read_bytes() == b"converted:b"
    assert e.converted == ["02.shn"]
    assert not (e.cfg.work_dir / str(job.id)).exists()
    assert e.tagged[0][2] == "Dave Matthews Band" and e.tagged[0][3] == b"JPEG"
    assert "tag warning" in j.warnings
    assert any("seeding" in ev["message"] for ev in e.db.list_events(job.id))
    # torrent data untouched
    assert sorted(p.name for p in e.content.iterdir()) == ["01.flac", "02.shn", "ffp.txt", "info.txt", "md5.txt"]
    assert (e.content / "02.shn").read_bytes() == b"b"
    # the info file picked was info.txt, not the larger ffp/md5 ones
    assert e.parsed[0].startswith("Dave Matthews Band")


def test_no_conversion_and_no_cover(env):
    e = env
    e.cfg.convert_to_flac = False
    e.cover = None
    job = run_to_seeding(e)
    j = e.db.get_job(job.id)
    assert j.state == JobState.seeding and e.converted == []
    assert not (e.target / "cover.jpg").exists()
    assert "no cover art found" in j.warnings


def test_multidisc_names(env):
    e = env
    orig = e.pl.hooks.tag_show

    def tag(files, info, album, artist, cover):
        orig(files, info, album, artist, cover)
        e.titles[files[0]] = (1, 1, "A/B")
        e.titles[files[1]] = (2, 1, "C")
        return []
    e.pl.hooks.tag_show = tag
    run_to_seeding(e)
    assert sorted(p.name for p in e.target.glob("*.flac")) == ["d1-01 A_B.flac", "d2-01 C.flac"]


def test_refuses_existing_nonempty_then_retry(env):
    e = env
    e.target.mkdir(parents=True)
    (e.target / "keep.txt").write_text("mine")
    job = run_to_seeding(e)
    j = e.db.get_job(job.id)
    assert j.state == JobState.error and j.error_detail.startswith("[tagged]")
    assert (e.target / "keep.txt").read_text() == "mine" and len(list(e.target.iterdir())) == 1
    assert any(ev["level"] == "error" for ev in e.db.list_events(job.id))
    (e.target / "keep.txt").unlink()
    j = e.pl.retry(job.id)
    assert j.state == JobState.seeding and j.error_detail is None
    assert e.qbit.adds == 1 and e.dt.downloads == 1          # retry resumed at publish, not at start


def test_empty_existing_destination_is_ok(env):
    e = env
    e.target.mkdir(parents=True)
    job = run_to_seeding(e)
    assert state(e, job) == JobState.seeding


def test_missing_info_file_error_and_retry_from_analyse(env):
    e = env
    (e.content / "info.txt").rename(e.content / "info.bak")
    (e.content / "ffp.txt").unlink()
    (e.content / "md5.txt").unlink()
    job = run_to_seeding(e)
    j = e.db.get_job(job.id)
    assert j.state == JobState.error and "[downloaded]" in j.error_detail
    (e.content / "info.bak").rename(e.content / "info.txt")
    assert e.pl.retry(job.id).state == JobState.seeding


def test_info_file_named_after_torrent_preferred(env):
    e = env
    (e.content / f"{NAME}.txt").write_text("short")
    files = e.pl._files(e.content)
    assert e.pl.find_info_file(files, NAME).name == f"{NAME}.txt"


def test_post_publish_command(env):
    e = env
    e.cfg.post_publish_command = 'echo "folder=$1"; exit 3'
    job = run_to_seeding(e)
    assert state(e, job) == JobState.seeding            # a failing hook never fails the job
    for t in e.pl.post_publish_threads:                 # the hook runs in the background
        t.join(10)
    msgs = [ev["message"] for ev in e.db.list_events(job.id)]
    assert any("exit 3" in m and f"folder={e.target}" in m for m in msgs)


def test_publish_idempotent_when_already_published(env):
    e = env
    job = run_to_seeding(e)
    e.db.update_job(job.id, state=JobState.tagged)      # simulate a crash after the copy
    assert e.pl.advance(job.id).state == JobState.seeding
    assert len(list(e.target.glob("*.flac"))) == 2


def test_qbit_error_state_fails_job(env):
    e = env
    job = e.pl.queue("7", NAME, "DMB")
    e.pl.advance(job.id)
    e.qbit.state = "missingFiles"
    e.pl.advance(job.id)
    j = e.db.get_job(job.id)
    assert j.state == JobState.error and "[downloading]" in j.error_detail
    e.qbit.state, e.qbit.progress = "downloading", 1.0
    assert e.pl.retry(job.id).state == JobState.seeding


def test_queue_validates_and_dedupes(env):
    e = env
    with pytest.raises(ValueError):
        e.pl.queue("1", "n", "nope")
    a = e.pl.queue("1", "n", "DMB")
    assert e.pl.queue("1", "n", "DMB").id == a.id


def test_purge_after_retention(env):
    e = env
    job = run_to_seeding(e)
    j = e.db.get_job(job.id)
    completed = datetime.fromisoformat(j.completed_at)
    assert e.pl.purge_expired(completed + timedelta(days=89)) == []
    assert e.qbit.deleted == []
    assert e.pl.purge_expired(completed + timedelta(days=91)) == [job.id]
    assert e.qbit.deleted == [("ab" * 20, True)]
    j = e.db.get_job(job.id)
    assert j.state == JobState.purged and j.purged_at
    assert e.pl.purge_expired(completed + timedelta(days=200)) == []


def test_cancel_before_complete_removes_torrent(env):
    e = env
    job = e.pl.queue("7", NAME, "DMB")
    e.pl.advance(job.id)
    assert e.pl.cancel(job.id).state == JobState.cancelled
    assert e.qbit.deleted == [("ab" * 20, True)]


def test_tick_advances_active_jobs(env):
    e = env
    a = e.pl.queue("1", NAME, "DMB")
    e.qbit.progress = 1.0
    e.pl.tick()                      # queued -> downloading
    e.pl.tick()                      # downloading -> ... -> seeding
    assert state(e, a) == JobState.seeding
    assert e.pl.tick() == []


# --- stalled / dead downloads ------------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

from app.db import now_iso  # noqa: E402


def _age(e, job, hours):
    """Pretend the job's last progress was `hours` ago."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    e.db.update_job(job.id, last_progress_at=ts)


def test_stalled_then_resumes(env):
    job = env.pl.queue("1", NAME, "DMB")
    env.pl.advance(job.id)
    assert state(env, job) == JobState.downloading
    _age(env, job, 25)
    env.pl.advance(job.id)
    j = env.db.get_job(job.id)
    assert j.state == JobState.stalled
    assert any("stalled" in ev["message"] for ev in env.db.list_events(job.id))
    env.qbit.progress = 0.4  # peers came back
    env.pl.advance(job.id)
    assert state(env, job) == JobState.downloading


def test_stalled_job_abandoned_and_retry_starts_over(env):
    job = env.pl.queue("1", NAME, "DMB")
    env.pl.advance(job.id)
    _age(env, job, 24 * 15)
    env.pl.advance(job.id)
    j = env.db.get_job(job.id)
    assert j.state == JobState.abandoned
    assert env.qbit.deleted == [("ab" * 20, True)]
    assert "Retry" in j.error_detail
    env.pl.retry(job.id)
    j = env.db.get_job(job.id)
    assert env.qbit.adds == 2 and j.state == JobState.downloading


def test_abandon_disabled_keeps_waiting(env):
    env.cfg.abandon_after_days = 0
    job = env.pl.queue("1", NAME, "DMB")
    env.pl.advance(job.id)
    _age(env, job, 24 * 365)
    env.pl.advance(job.id)
    assert state(env, job) == JobState.stalled and env.qbit.deleted == []


def test_dead_download_does_not_block_other_jobs(env):
    """One torrent stuck (or erroring) must not stop another from finishing."""
    dead = env.pl.queue("1", NAME, "DMB")
    env.pl.advance(dead.id)
    _age(env, dead, 25)
    good = env.pl.queue("2", NAME + ".b", "DMB")
    env.db.update_job(good.id, infohash="cd" * 20, state=JobState.downloading, last_progress_at=now_iso())

    real_info = env.qbit.info

    def info(h):
        d = real_info(h)
        if h == "ab" * 20:
            d["progress"] = 0.0
        else:
            d["progress"] = 1.0
            d["completion_on"] = 1_700_000_000
        return d

    env.qbit.info = info
    env.pl.tick()
    assert state(env, dead) == JobState.stalled
    assert state(env, good) not in (JobState.downloading, JobState.stalled, JobState.error)


def test_qbit_error_on_one_job_does_not_stop_tick(env):
    a = env.pl.queue("1", NAME, "DMB")
    b = env.pl.queue("2", NAME + ".b", "DMB")
    env.pl.advance(a.id)
    env.db.update_job(b.id, infohash="cd" * 20, state=JobState.downloading)
    real_info = env.qbit.info
    env.qbit.info = lambda h: (_ for _ in ()).throw(RuntimeError("boom")) if h == "ab" * 20 else dict(real_info(h), progress=1.0, completion_on=1_700_000_000)
    env.pl.tick()
    assert state(env, a) == JobState.error
    assert state(env, b) != JobState.downloading


def test_db_migrates_old_schema(tmp_path):
    import sqlite3
    p = tmp_path / "old.sqlite3"
    c = sqlite3.connect(p)
    c.executescript("""CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, torrent_id TEXT NOT NULL,
        torrent_name TEXT NOT NULL, destination TEXT NOT NULL, state TEXT NOT NULL, infohash TEXT, album TEXT,
        library_path TEXT, warnings TEXT NOT NULL DEFAULT '[]', error_detail TEXT, progress REAL NOT NULL DEFAULT 0,
        created_at TEXT, completed_at TEXT, purged_at TEXT);""")
    c.execute("INSERT INTO jobs (torrent_id, torrent_name, destination, state) VALUES ('1','x','DMB','queued')")
    c.commit(); c.close()
    db = Database(p)
    j = db.update_job(1, seeds=3)
    assert j.seeds == 3 and j.last_progress_at is None


def test_cancelled_after_download_is_still_purged(env):
    job = env.pl.queue("1", NAME, "DMB")
    env.pl.advance(job.id)
    env.qbit.progress = 1.0
    env.pl.advance(job.id)          # runs through to seeding
    env.pl.cancel(job.id)           # finished download: torrent keeps seeding
    assert env.qbit.deleted == []
    later = datetime.now(timezone.utc) + timedelta(days=91)
    assert env.pl.purge_expired(later) == [job.id]
    assert env.qbit.deleted == [("ab" * 20, True)]


def test_safe_filenames_keep_segues_readable():
    from app.pipeline import _safe
    assert _safe("PNP > Lie In Our Graves") == "PNP » Lie In Our Graves"
    assert _safe("So Much To Say -> Anyone Seen the Bridge") == "So Much To Say » Anyone Seen the Bridge"
    assert _safe("Bartender / Recently") == "Bartender - Recently"
    assert _safe('What "Would" You Say?') == "What _Would_ You Say_"


def test_post_publish_gets_host_path(env):
    e = env
    e.cfg.post_publish_command = 'echo "host=$DTDL_HOST_PATH"'
    job = run_to_seeding(e)
    for t in e.pl.post_publish_threads:
        t.join(10)
    msgs = [ev["message"] for ev in e.db.list_events(job.id)]
    assert any("host=/video/music/DMB_Live/2020/" in m for m in msgs), msgs


def test_corrupt_flac_stops_before_publish(env):
    e = env
    e.pl.hooks.verify_flac = lambda p: "ERROR while decoding data" if p.name.endswith(".flac") else None
    job = e.pl.queue("1", NAME, "DMB")
    e.pl.advance(job.id)
    e.qbit.progress = 1.0
    e.pl.advance(job.id)
    j = e.db.get_job(job.id)
    assert j.state == JobState.error and "integrity" in j.error_detail
    assert not e.target.exists()                        # nothing reached the library


def test_flac_test_on_real_and_broken_files(tmp_path):
    import shutil
    import subprocess
    from app.pipeline import flac_test
    if not shutil.which("flac") or not shutil.which("ffmpeg"):
        pytest.skip("needs flac and ffmpeg")
    good = tmp_path / "good.flac"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2", str(good)], check=True)
    assert flac_test(good) is None
    bad = tmp_path / "bad.flac"
    bad.write_bytes(good.read_bytes()[: good.stat().st_size // 2])     # truncated download
    assert flac_test(bad)
