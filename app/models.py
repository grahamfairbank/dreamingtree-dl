"""Shared data types. These are the contracts between modules; change them only with the integrator."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


@dataclass
class TorrentName:
    raw: str
    artist_code: str            # 'dmb', 'd&t' (also 'd+t'), 'dm', or 'other'
    date: date | None
    source_tokens: list[str]    # e.g. ['ca-11'], ['siriusxm', 'web-dl'], ['sbd']
    fmt: str | None             # 'flac16', 'flac24', 'shn', 'flac', 'mp3', ...


@dataclass
class ListingRow:
    torrent_id: str
    name: str                   # release/folder name as shown on the site
    artist: str | None
    show_date: date | None
    size_bytes: int | None
    seeders: int | None
    leechers: int | None
    snatches: int | None
    added: str | None           # site's own timestamp text
    download_url: str
    detail_url: str | None = None


@dataclass
class Track:
    number: int
    disc: int | None
    title: str
    segue: bool = False
    note: str | None = None     # footnote markers like '*' or '~' resolved to text if possible


@dataclass
class ShowInfo:
    artist: str | None = None
    venue: str | None = None
    city: str | None = None
    region: str | None = None       # US state / Canadian province code
    country: str | None = None      # None or 'USA' for domestic
    date: date | None = None
    source_line: str | None = None
    lineage: list[str] = field(default_factory=list)   # Taper/Source/Conversion lines, for COMMENT
    tracks: list[Track] = field(default_factory=list)


class JobState(str, Enum):
    queued = "queued"
    downloading = "downloading"
    stalled = "stalled"         # no download progress for cfg.stall_after_hours; still polled
    abandoned = "abandoned"     # gave up after cfg.abandon_after_days; torrent + partial data removed
    downloaded = "downloaded"
    analysing = "analysing"
    tagged = "tagged"
    copied = "copied"
    seeding = "seeding"
    purged = "purged"
    error = "error"
    cancelled = "cancelled"


@dataclass
class Job:
    id: int
    torrent_id: str
    torrent_name: str
    destination: str            # Destination.label
    state: JobState
    infohash: str | None = None
    album: str | None = None
    library_path: str | None = None
    warnings: list[str] = field(default_factory=list)
    error_detail: str | None = None
    progress: float = 0.0       # 0..1 while downloading
    last_progress_at: str | None = None   # last time progress moved (stall detection)
    seeds: int | None = None              # seeds seen in the swarm at the last poll
    created_at: str | None = None
    completed_at: str | None = None   # when the download finished; retention counts from here
    purged_at: str | None = None
