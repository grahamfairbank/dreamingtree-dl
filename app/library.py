"""Index of shows already in the music library, used for the browse table's "In library" column.

A show folder is any directory (up to MAX_DEPTH below a scan root) that directly holds audio.
Its date comes from the folder name, falling back to the ALBUM/DATE tag of its first audio file.

Match levels for a dreamingtree release, most to least certain:
  exact  - a library folder has the same release name (the library keeps torrent folder names)
  same   - same artist and date, same source type (e.g. both siriusxm): almost surely this show
  other  - same artist and date, different source: you have the show, not this recording
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

AUDIO = {".flac", ".shn", ".mp3", ".m4a", ".ape", ".wav", ".ogg"}
MAX_DEPTH = 3
# library sub-folder -> artist code when the folder name does not say
SCAN_ROOTS = {"DMB_Live": "dmb", "Dave_Tim": "d&t", "davematthews": "dm"}

_YMD = re.compile(r"(?<!\d)((?:19|20)\d\d)[-._/ ]?(\d{1,2})[-._/ ]?(\d{1,2})(?!\d)")
_MDY = re.compile(r"(?<!\d)(\d{1,2})[-._](\d{1,2})[-._](\d\d)(?!\d)")


@dataclass
class LibraryShow:
    path: str            # relative to the library root
    name: str            # folder name
    artist_code: str
    date: str | None     # ISO
    source: str          # 'taper' | 'siriusxm' | 'sbd' | ...


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def date_from_text(text: str) -> date | None:
    for m in _YMD.finditer(text):
        if dt := _valid(int(m[1]), int(m[2]), int(m[3])):
            return dt
    for m in _MDY.finditer(text):
        yy = int(m[3])
        if dt := _valid(1900 + yy if yy >= 90 else 2000 + yy, int(m[1]), int(m[2])):
            return dt
    return None


def classify_source(name: str) -> str:
    """Source type of a release/folder name, consistent with sources.source_suffix."""
    try:
        from app.models import ShowInfo
        from app.names import parse_torrent_name
        from app.sources import source_suffix
        return source_suffix(parse_torrent_name(name), ShowInfo()) or "taper"
    except Exception:
        n = name.lower()
        for token, src in (("sirius", "siriusxm"), ("sxm", "siriusxm"), ("sbd", "sbd"),
                           ("matrix", "matrix"), ("mtx", "matrix"), ("webcast", "webcast"), (".fm", "fm")):
            if token in n:
                return src
        return "taper"


def artist_from_name(name: str, default: str) -> str:
    n = name.lower()
    if n.startswith(("d&t", "d+t")):
        return "d&t"
    if n.startswith("dmb"):
        return "dmb"
    if n.startswith("dm") and n[2:3].isdigit():
        return "dm"
    return default


def _first_audio(folder: Path) -> Path | None:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO)
    return files[0] if files else None


def _tag_date(audio: Path) -> date | None:
    try:
        import mutagen
        f = mutagen.File(audio)
        if not f or not f.tags:
            return None
        for key in ("album", "ALBUM", "date", "DATE"):
            for v in f.tags.get(key, []) or []:
                if dt := date_from_text(str(v)):
                    return dt
    except Exception as e:
        log.debug("tag read failed for %s: %s", audio, e)
    return None


def scan(library_root: Path) -> list[LibraryShow]:
    shows: list[LibraryShow] = []
    for sub, default_artist in SCAN_ROOTS.items():
        base = library_root / sub
        if not base.is_dir():
            continue
        stack = [(base, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                children = list(folder.iterdir())
            except OSError as e:  # media-server has a few folders that give NFS I/O errors
                log.warning("library scan: cannot list %s: %s", folder, e)
                continue
            if folder != base and any(c.suffix.lower() in AUDIO for c in children if c.is_file()):
                dt = date_from_text(folder.name)
                if dt is None and folder.parent != base:  # e.g. "<show>/ALAC (Apple Lossless)"
                    dt = date_from_text(folder.parent.name)
                if dt is None and (audio := _first_audio(folder)):
                    dt = _tag_date(audio)
                shows.append(LibraryShow(
                    path=str(folder.relative_to(library_root)), name=folder.name,
                    artist_code=artist_from_name(folder.name, default_artist),
                    date=dt.isoformat() if dt else None, source=classify_source(folder.name)))
                continue
            if depth < MAX_DEPTH:
                stack.extend((c, depth + 1) for c in children if c.is_dir() and not c.name.startswith("."))
    return shows


def _norm(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"\.(gf|fixed(-v\d+)?)$", "", n)
    return re.sub(r"[\s_]+", ".", n)


class LibraryIndex:
    """Thread-safe in-memory index, persisted to JSON so restarts don't need a rescan."""

    def __init__(self, library_root: Path, cache_file: Path):
        self.root = library_root
        self.cache_file = cache_file
        self._lock = threading.Lock()
        self._shows: list[LibraryShow] = []
        self.scanned_at: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.cache_file.read_text())
            self._set([LibraryShow(**s) for s in data["shows"]], data.get("scanned_at"))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _set(self, shows: list[LibraryShow], scanned_at: str | None) -> None:
        by_name = {_norm(s.name): s for s in shows}
        by_day: dict[tuple[str, str], list[LibraryShow]] = {}
        for s in shows:
            if s.date:
                by_day.setdefault((s.artist_code, s.date), []).append(s)
        with self._lock:
            self._shows, self._by_name, self._by_day, self.scanned_at = shows, by_name, by_day, scanned_at

    def rescan(self) -> int:
        shows = scan(self.root)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._set(shows, stamp)
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps({"scanned_at": stamp, "shows": [asdict(s) for s in shows]}))
        except OSError as e:
            log.warning("cannot write library index: %s", e)
        log.info("library scan: %d show folders", len(shows))
        return len(shows)

    def match(self, name: str, artist_code: str, show_date: str | None, source: str) -> dict | None:
        """{'level': 'exact'|'same'|'other', 'paths': [...], 'sources': [...]} or None."""
        with self._lock:
            hit = self._by_name.get(_norm(name)) if self._shows else None
            if hit:
                return {"level": "exact", "paths": [hit.path], "sources": [hit.source]}
            same_day = self._by_day.get((artist_code, show_date or ""), []) if show_date else []
        if not same_day:
            return None
        level = "same" if any(s.source == source for s in same_day) else "other"
        return {"level": level, "paths": [s.path for s in same_day],
                "sources": sorted({s.source for s in same_day})}

    def __len__(self) -> int:
        return len(self._shows)
