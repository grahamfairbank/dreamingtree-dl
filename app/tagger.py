"""Write FLAC tags and the embedded cover.

Tag mapping is in docs/ARCHITECTURE.md ("Tag mapping"). Files are matched to setlist entries by order;
when the counts differ nothing is guessed: existing TITLE (or the filename) is kept and a warning returned.
"""
from __future__ import annotations

import re
from pathlib import Path

from mutagen.flac import FLAC, Picture

from .models import ShowInfo, Track

# Tags we own and rewrite. Everything else (replaygain_*, fingerprint/acoustid, encoder...) is left alone.
OWNED_KEYS = {
    "title", "album", "artist", "albumartist", "album artist", "tracknumber", "tracktotal", "totaltracks",
    "discnumber", "disctotal", "totaldiscs", "date", "year", "genre", "comment", "description",
}

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def cover_mime(data: bytes) -> str | None:
    if data.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(PNG_MAGIC):
        return "image/png"
    return None


def _picture(data: bytes, mime: str) -> Picture:
    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = mime
    pic.desc = "Cover"
    pic.data = data
    w = h = 0
    if mime == "image/png" and len(data) >= 24:
        w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        pic.depth = 24
    elif mime == "image/jpeg":
        w, h = _jpeg_size(data)
        pic.depth = 24
    pic.width, pic.height = w, h
    return pic


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    try:
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    except Exception:
        pass
    return 0, 0


def _strip_track_number(title: str) -> str:
    """'01 Intro' / '1. Intro' / 'd1t01 Intro' -> 'Intro' (tapers often put the number in TITLE).
    Leaves '#41' and titles that would be empty alone."""
    rest = re.sub(r"(?i)^\s*(?:d\d+\s*)?t?\d{1,3}\s*(?:[.)\-_:]\s*|\s+)", "", title, count=1).strip()
    return rest or title


def _title_from_filename(path: Path) -> str:
    """'03 Song.flac', 'd1t03 - Song', 'dmb2022-07-09.t03.Song' -> 'Song'; 'Track NN' if only a number remains."""
    stem = path.stem
    m = re.match(r"(?i)^(?:dmb|d[+&]t|dm)\S*?[.\-_ ](?:d\d+)?t(\d{1,3})\b[\s._)\-]*(.*)$", stem)
    if m:
        num, rest = m.group(1), m.group(2)
    else:
        m = re.match(r"(?i)^\s*(?:d\d+\s*)?(?:t(?=\d))?(\d{1,3})\b[\s._)\-]*(.*)$", stem)
        num, rest = (m.group(1), m.group(2)) if m else (None, stem)
    rest = re.sub(r"_+", " ", rest).strip(" .-")
    if rest:
        return rest
    return f"Track {num}" if num else stem


def _show_date(album: str, info: ShowInfo) -> str | None:
    m = re.match(r"\s*(\d{4}-\d\d-\d\d)", album or "")
    if m:
        return m.group(1)
    return info.date.isoformat() if info.date else None


def _clear_owned(f: FLAC) -> None:
    for k in list(f.keys()):
        if k.lower() in OWNED_KEYS:
            del f[k]


def _layout(tracks: list[Track]) -> list[tuple[int | None, int, int]]:
    """For each setlist entry: (disc, number within disc, tracks in disc). Positional, not trusting file numbers."""
    discs = [t.disc for t in tracks]
    multi = len({d for d in discs if d is not None}) > 1
    out: list[tuple[int | None, int, int]] = []
    if not multi:
        return [(None, i + 1, len(tracks)) for i in range(len(tracks))]
    counts: dict[int | None, int] = {}
    for d in discs:
        counts[d] = counts.get(d, 0) + 1
    seen: dict[int | None, int] = {}
    for d in discs:
        seen[d] = seen.get(d, 0) + 1
        out.append((d, seen[d], counts[d]))
    return out


def _comment(info: ShowInfo, track: Track | None) -> str:
    lines = list(info.lineage)
    if track is not None:
        if track.note:
            lines.append(f"Note: {track.note}")
        if track.segue:
            lines.append("Segues into the next track (>)")
    return "\n".join(lines)


def tag_show(files: list[Path], info: ShowInfo, album: str, artist: str, cover: bytes | None) -> list[str]:
    """Tag files in place (these are work copies, never torrent data). Returns warnings."""
    warnings: list[str] = []
    files = [Path(p) for p in files]
    matched = bool(info.tracks) and len(info.tracks) == len(files)
    if not info.tracks:
        warnings.append("no setlist found in the info file; kept existing/filename titles")
    elif not matched:
        warnings.append(
            f"setlist has {len(info.tracks)} tracks but there are {len(files)} files; "
            "kept existing/filename titles, check the track titles")

    pic = None
    if cover:
        mime = cover_mime(cover)
        if mime:
            pic = _picture(cover, mime)
        else:
            warnings.append("cover image is not JPEG or PNG; not embedded")

    layout = _layout(info.tracks) if matched else []
    show_date = _show_date(album, info)
    multi_disc = matched and any(d is not None for d, _, _ in layout)

    for idx, path in enumerate(files):
        f = FLAC(path)
        old_title = (f.get("title") or [None])[0]
        _clear_owned(f)
        track = info.tracks[idx] if matched else None
        if track is not None:
            title = track.title
        else:
            title = _strip_track_number(old_title) if old_title else _title_from_filename(path)
        f["title"] = [title]
        f["album"] = [album]
        f["artist"] = [artist]
        f["albumartist"] = [artist]
        f["genre"] = ["Live"]
        if show_date:
            f["date"] = [show_date]
        if matched:
            disc, num, total = layout[idx]
            f["tracknumber"] = [str(num)]
            f["tracktotal"] = [str(total)]
            if multi_disc and disc is not None:
                f["discnumber"] = [str(disc)]
                f["disctotal"] = [str(len({d for d, _, _ in layout if d is not None}))]
        else:
            f["tracknumber"] = [str(idx + 1)]
            f["tracktotal"] = [str(len(files))]
        comment = _comment(info, track)
        if comment:
            f["comment"] = [comment]
        if pic is not None:
            f.clear_pictures()
            f.add_picture(pic)
        f.save()
    return warnings
