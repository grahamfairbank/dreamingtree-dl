"""Moves a Job through the JobState machine.

Paths: qBittorrent saves to cfg.torrents_dir/<torrent_name> (never modified); tagging happens in
cfg.work_dir/<job_id>; publishing copies the work dir to the destination, then deletes it.

State -> step run by advance():
  queued      start    add the torrent to qBittorrent                 -> downloading
  downloading poll     update progress; at 100%                       -> downloaded
  downloaded  analyse  info file, album name, cover                   -> analysing
  analysing   tag      convert/copy audio to the work dir, tag it     -> tagged
  tagged      publish  copy to the library, post-publish command      -> copied
  copied      finish                                                  -> seeding
  seeding     (waits for retention)  purge_expired()                  -> purged
Any step failure sets state=error with error_detail "[<state>] message"; retry() resumes from that state.
Every step is idempotent, so a crash or retry never duplicates or corrupts anything.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .db import Database, now_iso
from .models import Job, JobState, ShowInfo, TorrentName

log = logging.getLogger("dtdl.pipeline")

AUDIO_CONVERT = {".shn", ".ape", ".wav"}
AUDIO_EXTS = {".flac"} | AUDIO_CONVERT
INFO_EXCLUDE = re.compile(r"ffp|md5|st5|fingerprint|checksum|sha1", re.I)
ARTIST_NAMES = {
    "dmb": "Dave Matthews Band",
    "d&t": "Dave Matthews & Tim Reynolds",
    "d+t": "Dave Matthews & Tim Reynolds",
    "dm": "Dave Matthews",
}
WAITING = {JobState.downloading, JobState.stalled, JobState.seeding, JobState.error,
           JobState.cancelled, JobState.purged, JobState.abandoned}
ACTIVE = [JobState.queued, JobState.downloading, JobState.stalled, JobState.downloaded, JobState.analysing,
          JobState.tagged, JobState.copied]
QBIT_BAD_STATES = {"error", "missingFiles"}


class PipelineError(RuntimeError):
    pass


def _lazy(module: str, attr: str) -> Callable[..., Any]:
    def call(*a, **kw):
        import importlib
        return getattr(importlib.import_module(f"app.{module}"), attr)(*a, **kw)
    return call


def flac_test(path: Path) -> str | None:
    """None if `flac -t` decodes the file cleanly, else a short error."""
    try:
        r = subprocess.run(["flac", "-t", "-s", str(path)], capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        return None  # flac not installed (dev boxes): skip rather than fail every job
    except subprocess.TimeoutExpired:
        return "flac -t timed out"
    return None if r.returncode == 0 else ((r.stderr or r.stdout).strip().splitlines() or ["failed"])[-1][:200]


def read_flac_tags(path: Path) -> tuple[int | None, int | None, str | None]:
    """(disc, track, title) from a FLAC file's tags; used to name files after tagging."""
    from mutagen.flac import FLAC
    f = FLAC(str(path))

    def num(key: str) -> int | None:
        v = (f.get(key) or [""])[0].split("/")[0].strip()
        return int(v) if v.isdigit() else None
    return num("discnumber"), num("tracknumber"), (f.get("title") or [None])[0]


@dataclass
class Hooks:
    """Metadata functions, injected so tests can use fakes. Defaults resolve the real modules lazily."""
    parse_torrent_name: Callable[[str], TorrentName] = _lazy("names", "parse_torrent_name")
    parse_info_file: Callable[[str], ShowInfo] = _lazy("infofile", "parse_info_file")
    source_suffix: Callable[[TorrentName, ShowInfo], str] = _lazy("sources", "source_suffix")
    album_name: Callable[..., str] = _lazy("albumname", "album_name")
    find_cover: Callable[..., bytes | None] = _lazy("art", "find_cover")
    tag_show: Callable[..., list[str]] = _lazy("tagger", "tag_show")
    to_flac: Callable[[Path, Path], Path] = _lazy("convert", "to_flac")
    read_tags: Callable[[Path], tuple[int | None, int | None, str | None]] = read_flac_tags
    verify_flac: Callable[[Path], str | None] = None  # type: ignore[assignment]  # set below

    def __post_init__(self):
        if self.verify_flac is None:
            self.verify_flac = flac_test


def _natural(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))]


def _safe(name: str) -> str:
    # segues and medleys first, written the way the author's library already does: 'A » B', 'A - B'
    name = re.sub(r"\s*(?:->|>)\s*", " » ", name)
    name = re.sub(r"\s+/\s+", " - ", name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name or "_"


def _parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class Pipeline:
    def __init__(self, cfg: Config, db: Database, qbit, dt_client, hooks: Hooks | None = None,
                 art_cache_dir: Path | None = None):
        self.cfg = cfg
        self.art_cache_dir = art_cache_dir
        self.post_publish_threads: list[threading.Thread] = []
        self.db = db
        self.qbit = qbit
        self.dt = dt_client
        self.hooks = hooks or Hooks()
        self._locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------ public API
    def queue(self, torrent_id: str, torrent_name: str, destination: str) -> Job:
        """Create a job (no I/O besides the DB). An active job for the same torrent is returned as is."""
        try:
            self.cfg.destination(destination)
        except KeyError as e:
            raise ValueError(str(e)) from None
        for j in self.db.list_jobs(ACTIVE + [JobState.error]):
            if j.torrent_id == torrent_id:
                return j
        job = self.db.create_job(torrent_id, torrent_name, destination)
        self.db.add_event(job.id, "info", f"queued for {destination}")
        return job

    def advance(self, job_id: int) -> Job:
        """Run steps until the job needs to wait (still downloading, seeding) or fails."""
        with self._job_lock(job_id):
            job = self._get(job_id)
            for _ in range(12):
                step = self._steps().get(job.state)
                if step is None:
                    break
                before = job.state
                try:
                    step(job)
                except Exception as e:  # noqa: BLE001 - every failure must land in the job state
                    return self._fail(job, e)
                job = self._get(job_id)
                if job.state == before or job.state in WAITING:
                    break
            return job

    def tick(self) -> list[int]:
        """Advance every active job (called by the scheduler). Returns ids that were advanced."""
        ids = []
        for job in reversed(self.db.list_jobs(ACTIVE)):
            ids.append(job.id)
            try:
                self.advance(job.id)
            except Exception:  # noqa: BLE001
                log.exception("advance failed for job %s", job.id)
        return ids

    def retry(self, job_id: int) -> Job:
        """Restart a failed job from the step that failed, then advance it."""
        with self._job_lock(job_id):
            job = self._get(job_id)
            if job.state == JobState.abandoned:  # start over: fetch the torrent again
                self.db.update_job(job_id, state=JobState.queued, infohash=None, progress=0.0,
                                   last_progress_at=None, seeds=None, error_detail=None)
                self.db.add_event(job_id, "info", "retrying abandoned download from scratch")
            elif job.state == JobState.error:
                state = self._failed_state(job)
                self.db.update_job(job_id, state=state, error_detail=None)
                self.db.add_event(job_id, "info", f"retrying from {state.value}")
            else:
                raise ValueError(f"job {job_id} is not in error or abandoned state")
        return self.advance(job_id)  # outside the lock: advance() takes it itself

    def cancel(self, job_id: int) -> Job:
        """Stop a job. A torrent that has not finished downloading is removed with its partial data;
        a completed one is left seeding in qBittorrent (retention purge cleans it up)."""
        with self._job_lock(job_id):
            job = self._get(job_id)
            if job.state in (JobState.purged, JobState.cancelled, JobState.abandoned):
                return job
            if job.infohash and not job.completed_at:
                try:
                    self.qbit.delete(job.infohash, True)
                except Exception as e:  # noqa: BLE001
                    self.db.add_event(job_id, "warn", f"could not remove torrent: {e}")
            self._rmtree(self._work(job))
            self.db.add_event(job_id, "info", "cancelled")
            return self.db.update_job(job_id, state=JobState.cancelled)

    def purge_expired(self, now: datetime | None = None) -> list[int]:
        """Remove torrents (with data) whose download finished more than retention_days ago."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.cfg.retention_days)
        done = []
        for job in self.db.list_jobs([JobState.seeding, JobState.cancelled]):
            if not job.completed_at or _parse_ts(job.completed_at) > cutoff:
                continue
            if job.state == JobState.cancelled and not job.infohash:
                continue
            with self._job_lock(job.id):
                try:
                    self._purge(self._get(job.id))
                    done.append(job.id)
                except Exception as e:  # noqa: BLE001
                    self.db.add_event(job.id, "error", f"purge failed: {e}")
                    log.exception("purge failed for job %s", job.id)
        return done

    # ------------------------------------------------------------------ plumbing
    def _steps(self) -> dict[JobState, Callable[[Job], None]]:
        return {
            JobState.queued: self._start, JobState.downloading: self._poll, JobState.stalled: self._poll,
            JobState.downloaded: self._analyse, JobState.analysing: self._tag,
            JobState.tagged: self._publish, JobState.copied: self._finish,
        }

    def _job_lock(self, job_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks[job_id]

    def _get(self, job_id: int) -> Job:
        job = self.db.get_job(job_id)
        if job is None:
            raise KeyError(f"no such job {job_id}")
        return job

    def _fail(self, job: Job, exc: Exception) -> Job:
        log.warning("job %s failed in %s: %s", job.id, job.state.value, exc)
        detail = f"[{job.state.value}] {exc}"
        self.db.add_event(job.id, "error", detail)
        return self.db.update_job(job.id, state=JobState.error, error_detail=detail)

    @staticmethod
    def _failed_state(job: Job) -> JobState:
        m = re.match(r"\[(\w+)\]", job.error_detail or "")
        if m:
            try:
                s = JobState(m.group(1))
                if s not in (JobState.error, JobState.cancelled, JobState.purged, JobState.seeding):
                    return s
            except ValueError:
                pass
        if not job.infohash:
            return JobState.queued
        return JobState.downloading if not job.completed_at else JobState.downloaded

    def _work(self, job: Job) -> Path:
        return self.cfg.work_dir / str(job.id)

    @staticmethod
    def _rmtree(p: Path) -> None:
        shutil.rmtree(p, ignore_errors=True)

    def _event(self, job: Job, msg: str, level: str = "info") -> None:
        self.db.add_event(job.id, level, msg)

    # ------------------------------------------------------------------ steps
    def _start(self, job: Job) -> None:
        if not job.infohash:
            try:
                data = self.dt.download_torrent(job.torrent_id)
            except Exception:  # noqa: BLE001 - session may have expired: log in once and retry
                self.dt.login()
                data = self.dt.download_torrent(job.torrent_id)
            infohash = self.qbit.add(data, str(self.cfg.torrents_dir), f"dtdl-{job.id}")
            self.db.update_job(job.id, infohash=infohash)
            self._event(job, f"added to qBittorrent ({infohash})")
        self.db.update_job(job.id, state=JobState.downloading, last_progress_at=now_iso())

    def _poll(self, job: Job, now: datetime | None = None) -> None:
        if not job.infohash:
            raise PipelineError("job has no infohash")
        info = self.qbit.info(job.infohash)
        if info.get("state") in QBIT_BAD_STATES:
            raise PipelineError(f"qBittorrent reports state {info['state']}")
        progress = float(info.get("progress", 0.0))
        seeds = info.get("num_complete", info.get("num_seeds"))
        seeds = int(seeds) if isinstance(seeds, (int, float)) and seeds >= 0 else None
        if progress < 1.0:
            self._check_stall(job, progress, seeds, now or datetime.now(timezone.utc))
            return
        done = info.get("completion_on") or 0
        completed = (datetime.fromtimestamp(done, timezone.utc).isoformat(timespec="seconds")
                     if done and done > 0 else now_iso())
        self.db.update_job(job.id, state=JobState.downloaded, progress=1.0,
                           completed_at=job.completed_at or completed)
        self._event(job, "download complete")

    def _check_stall(self, job: Job, progress: float, seeds: int | None, now: datetime) -> None:
        """Track progress; flag a job stalled after stall_after_hours and give up after
        abandon_after_days. One dead torrent never blocks other jobs."""
        if abs(progress - job.progress) > 1e-6:
            self.db.update_job(job.id, progress=progress, seeds=seeds, last_progress_at=now_iso(),
                               state=JobState.downloading)
            if job.state == JobState.stalled:
                self._event(job, f"download resumed ({progress:.0%}, {seeds} seeds)")
            return
        if seeds != job.seeds:
            self.db.update_job(job.id, seeds=seeds)
        since = _parse_ts(job.last_progress_at or job.created_at or now_iso())
        idle = now - since
        abandon = self.cfg.abandon_after_days
        if abandon > 0 and idle >= timedelta(days=abandon):
            try:
                self.qbit.delete(job.infohash, True)
            except Exception as e:  # noqa: BLE001
                self._event(job, f"could not remove stalled torrent: {e}", "warn")
            self.db.update_job(job.id, state=JobState.abandoned,
                               error_detail=f"no progress for {idle.days} days (seeds: {seeds}); "
                                            "torrent removed. Retry starts it over.")
            self._event(job, f"abandoned after {idle.days} days without progress", "warn")
        elif job.state != JobState.stalled and idle >= timedelta(hours=self.cfg.stall_after_hours):
            self.db.update_job(job.id, state=JobState.stalled)
            self._event(job, f"stalled: no progress for {idle.total_seconds() / 3600:.0f}h "
                             f"at {progress:.0%} (seeds: {seeds})", "warn")

    # -- locating torrent data
    def _content_path(self, job: Job) -> Path:
        info = self.qbit.info(job.infohash)
        raw = info.get("content_path") or ""
        p = Path(raw)
        if not p.exists():
            save = info.get("save_path") or ""
            if raw and save and raw.startswith(save.rstrip("/") + "/"):
                alt = self.cfg.torrents_dir / raw[len(save.rstrip("/")) + 1:]
                if alt.exists():
                    p = alt
        if not raw or not p.exists():
            raise PipelineError(f"torrent content not found at {raw!r}")
        return p

    @staticmethod
    def _files(content: Path) -> list[Path]:
        return [content] if content.is_file() else sorted((f for f in content.rglob("*") if f.is_file()), key=_natural)

    @staticmethod
    def _decode_text(p: Path) -> str:
        raw = p.read_bytes()
        for enc in ("utf-8-sig", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", errors="replace")

    def find_info_file(self, files: list[Path], torrent_name: str) -> Path | None:
        txts = [f for f in files if f.suffix.lower() == ".txt"]
        named = [f for f in txts if f.stem.lower() == torrent_name.lower()]
        if named:
            return named[0]
        cands = [f for f in txts if not INFO_EXCLUDE.search(f.name)]
        return max(cands, key=lambda f: f.stat().st_size) if cands else None

    def _load_show(self, job: Job) -> tuple[Path, list[Path], Path, ShowInfo, TorrentName]:
        content = self._content_path(job)
        files = self._files(content)
        info_file = self.find_info_file(files, job.torrent_name)
        if info_file is None:
            raise PipelineError("no info .txt file found in the torrent")
        info = self.hooks.parse_info_file(self._decode_text(info_file))
        name = self.hooks.parse_torrent_name(job.torrent_name)
        return content, files, info_file, info, name

    def _analyse(self, job: Job) -> None:
        content, files, info_file, info, name = self._load_show(job)
        show_date = name.date or info.date  # info files sometimes carry the wrong date
        if show_date is None:
            raise PipelineError("could not determine the show date")
        suffix = self.hooks.source_suffix(name, info)
        album = self.hooks.album_name(show_date, info.city, info.region, info.country, suffix,
                                      fallback=info.venue)
        show_dir = content if content.is_dir() else content.parent
        art = self.cfg.art
        cover = self.hooks.find_cover(show_dir, show_date, name.artist_code, self.art_cache_dir,
                                      use_torrent_image=art.use_torrent_image,
                                      use_dmbalmanac=art.use_dmbalmanac)
        work = self._work(job)
        work.mkdir(parents=True, exist_ok=True)
        cover_path = work / "cover.jpg"
        if cover:
            cover_path.write_bytes(cover)
        elif cover_path.exists():
            cover_path.unlink()
        warnings = list(job.warnings)
        if not cover and "no cover art found" not in warnings:
            warnings.append("no cover art found")
        self.db.update_job(job.id, state=JobState.analysing, album=album, warnings=warnings)
        self._event(job, f"album: {album}; info file: {info_file.name}; cover: {'yes' if cover else 'no'}")

    def _artist(self, name: TorrentName, info: ShowInfo) -> str:
        return ARTIST_NAMES.get(name.artist_code) or info.artist or "Unknown Artist"

    def _tag(self, job: Job) -> None:
        content, files, _info_file, info, name = self._load_show(job)
        if not job.album:
            raise PipelineError("album not computed")
        work = self._work(job)
        cover_path = work / "cover.jpg"
        cover = cover_path.read_bytes() if cover_path.exists() else None
        # start clean: remove leftovers of an earlier attempt (but keep the cover)
        for child in work.iterdir() if work.exists() else []:
            if child != cover_path:
                self._rmtree(child) if child.is_dir() else child.unlink()
        work.mkdir(parents=True, exist_ok=True)
        stage = work / "_t"
        audio = [f for f in files if f.suffix.lower() in AUDIO_EXTS]
        if not audio:
            raise PipelineError("no audio files found in the torrent")
        staged: list[Path] = []
        warnings = [w for w in job.warnings if w == "no cover art found"]
        for i, src in enumerate(audio):
            d = stage / f"{i:03d}"
            d.mkdir(parents=True)
            if src.suffix.lower() in AUDIO_CONVERT and self.cfg.convert_to_flac:
                staged.append(self.hooks.to_flac(src, d))
            else:
                dst = d / src.name
                shutil.copyfile(src, dst)
                staged.append(dst)
                if src.suffix.lower() != ".flac":
                    warnings.append(f"{src.name}: not converted to FLAC (convert_to_flac is off)")
        if self.cfg.verify_flac:
            bad = [(p, err) for p in staged if p.suffix.lower() == ".flac" and (err := self.hooks.verify_flac(p))]
            if bad:
                raise PipelineError("FLAC integrity check failed (corrupt or incomplete download?): "
                                    + "; ".join(f"{p.name}: {err}" for p, err in bad[:5]))
        warnings += list(self.hooks.tag_show(staged, info, job.album, self._artist(name, info), cover) or [])
        self._name_files(staged, work)
        self._rmtree(stage)
        self.db.update_job(job.id, state=JobState.tagged, warnings=list(dict.fromkeys(warnings)))
        self._event(job, f"tagged {len(staged)} files")

    def _name_files(self, staged: list[Path], work: Path) -> None:
        """Rename tagged files to `NN Title.flac` (`dN-NN Title.flac` when multi-disc)."""
        rows = []
        for i, p in enumerate(staged, 1):
            try:
                disc, track, title = self.hooks.read_tags(p)
            except Exception:  # noqa: BLE001 - unreadable tags: fall back to file order/name
                disc = track = title = None
            if not title:
                title = re.sub(r"^(d\d+)?[\s\-_.]*t?\d+[\s\-_.]*", "", p.stem) or p.stem
            rows.append((p, disc, track or i, title))
        multi = len({d for _, d, _, _ in rows if d}) > 1 or any((d or 1) > 1 for _, d, _, _ in rows)
        used: set[str] = set()
        for p, disc, track, title in rows:
            base = f"{'d%d-' % (disc or 1) if multi else ''}{track:02d} {_safe(title)}"
            final, n = f"{base}.flac", 1
            while final.lower() in used:
                n += 1
                final = f"{base} ({n}).flac"
            used.add(final.lower())
            p.replace(work / final)

    # -- publish
    def destination_dir(self, job: Job, name: TorrentName, info: ShowInfo) -> Path:
        dest = self.cfg.destination(job.destination)
        tmpl = dest.folder_template
        code = name.artist_code
        alias = {"d+t": "d&t", "d&t": "d+t"}.get(code)
        template = tmpl.get(code) or (tmpl.get(alias) if alias else None) or tmpl.get("default")
        if not template:
            raise PipelineError(f"no folder_template for {code!r} and no 'default'")
        show_date = name.date or info.date  # info files sometimes carry the wrong date
        values = {
            "torrent_name": _safe(job.torrent_name), "album": _safe(job.album or ""),
            "artist": _safe(self._artist(name, info)), "year": str(show_date.year) if show_date else "",
        }
        rel = Path(template.format(**values))
        if rel.is_absolute() or ".." in rel.parts:
            raise PipelineError(f"unsafe folder template result {str(rel)!r}")
        return self.cfg.to_container_path(dest.path) / rel

    def _publish(self, job: Job) -> None:
        content, files, info_file, info, name = self._load_show(job)
        target = self.destination_dir(job, name, info)
        work = self._work(job)
        if job.library_path and Path(job.library_path) == target and target.is_dir():
            pass  # already published by an earlier attempt
        else:
            if target.exists() and (not target.is_dir() or any(target.iterdir())):
                raise PipelineError(f"destination already exists and is not empty: {target}")
            if not work.is_dir() or not any(work.glob("*.flac")):
                self._event(job, "work directory is missing; redoing analysis and tagging", "warn")
                self.db.update_job(job.id, state=JobState.downloaded)
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.parent / f".{target.name}.dtdl-tmp"
            self._rmtree(tmp)
            tmp.mkdir()
            try:
                for f in sorted(work.iterdir()):
                    if f.is_file():
                        shutil.copyfile(f, tmp / f.name)
                shutil.copyfile(info_file, tmp / info_file.name)
                if target.is_dir():
                    target.rmdir()          # empty directory we are allowed to replace
                tmp.rename(target)
            except Exception:
                self._rmtree(tmp)
                raise
            self.db.update_job(job.id, library_path=str(target))
            self._event(job, f"published to {target}")
        self._rmtree(work)
        self._run_post_publish(job, target)
        self.db.update_job(job.id, state=JobState.copied)

    def _run_post_publish(self, job: Job, folder: Path) -> None:
        """Run the hook in the background (Plex deep analysis can take many minutes) so it never
        holds up this job or others. $1 is the container path; DTDL_HOST_PATH the library-side one."""
        cmd = (self.cfg.post_publish_command or "").strip()
        if not cmd:
            return
        env = dict(os.environ)
        host = self.cfg.to_host_path(folder)
        if host:
            env["DTDL_HOST_PATH"] = host
        t = threading.Thread(target=self._post_publish_worker, args=(job.id, cmd, folder, env),
                             name=f"dtdl-post-publish-{job.id}", daemon=True)
        self.post_publish_threads.append(t)
        t.start()
        self._event(job, f"post_publish_command started for {host or folder}")

    def _post_publish_worker(self, job_id: int, cmd: str, folder: Path, env: dict) -> None:
        timeout = max(60.0, float(self.cfg.post_publish_timeout_minutes) * 60)
        try:
            r = subprocess.run(["/bin/sh", "-c", cmd, "dtdl-post-publish", str(folder)],
                               capture_output=True, text=True, timeout=timeout, env=env)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            self.db.add_event(job_id, "info" if r.returncode == 0 else "warn",
                              f"post_publish_command exit {r.returncode}" + (f": {out[-2000:]}" if out else ""))
        except Exception as e:  # noqa: BLE001 - never fail the job on the hook
            self.db.add_event(job_id, "warn", f"post_publish_command failed: {e}")

    def _finish(self, job: Job) -> None:
        self.db.update_job(job.id, state=JobState.seeding)
        self._event(job, "seeding")

    # -- purge
    def _purge(self, job: Job) -> None:
        if job.infohash:
            self.qbit.delete(job.infohash, True)
        self._rmtree(self._work(job))
        self.db.update_job(job.id, state=JobState.purged, purged_at=now_iso())
        self._event(job, "purged (torrent and data removed)")
