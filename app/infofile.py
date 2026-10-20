"""Parse a show's info .txt into ShowInfo.

Info files are free-form. The header (before the setlist) usually holds artist, venue,
"City, ST" and a date in some order; labelled lines (Taper:, Source:, ...) hold lineage;
numbered lines hold the setlist. Everything is heuristic and defensive: unknown -> None.
"""
from __future__ import annotations

import difflib
import re
from datetime import date

from .albumname import country_name, is_uk_subregion, region_code
from .models import ShowInfo, Track

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_WD = r"(?:(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?\.?,?\s+)?"
_ORD = r"(?:st|nd|rd|th)?"
_DATE_RES = [
    re.compile(rf"\b{_WD}{_MON}\s+(?P<d>\d{{1,2}}){_ORD}(?:\s*,\s*|\s+)(?P<y>\d{{4}})\b", re.I),
    re.compile(rf"\b{_WD}(?P<d>\d{{1,2}}){_ORD}\s+(?:of\s+)?{_MON}\s*,?\s+(?P<y>\d{{4}})\b", re.I),
    re.compile(r"\b(?P<y>(?:19|20)\d\d)[-./](?P<m>\d{1,2})[-./](?P<d>\d{1,2})\b"),
    re.compile(r"(?<![\d.:])(?P<m>\d{1,2})[-/.](?P<d>\d{1,2})[-/.](?P<y>(?:19|20)?\d\d)(?![\d:])"),
]

LABEL_RE = re.compile(r"^\s*(?P<k>[A-Za-z][A-Za-z /&]{1,24}?)\s*:\s*(?P<v>.*)$")
LINEAGE_LABELS = {
    "taper", "tapers", "taped by", "recorded by", "source", "source info", "sources", "lineage",
    "conversion", "transfer", "transferred by", "transfered by", "equipment", "gear", "mastering",
    "capturing and splitting", "converted by", "encoded by", "seeded by",
}
SOURCE_LABELS = {"source", "source info", "sources"}
OTHER_LABELS = {"location", "notes", "note", "comments", "total", "total time", "tracking", "setlist",
                "venue", "date", "city", "artist", "disc", "set", "cd", "encore", "info", "guests",
                "pre-amp", "preamp", "recorder", "mics", "mic", "show", "type"}

TIME_TAIL = re.compile(r"[\s\-–—~]*[\[(]?\d{1,3}:\d{2}(?::\d{2})?(?:[.:]\d{1,3})?[\])]?\s*$")
SEGUE_TAIL = re.compile(r"\s*(?:-+>+|=+>+|>+|»+|→)\s*$")
MARK_TAIL = re.compile(r"(?:(?<=\w)|(?<=\)))[\*~\+\^#@%]+\s*$|\s+[\*~\+\^#@%=&!\$]+\s*$")
DISC_RE = re.compile(r"^\W*(?:disc|disk|cd)\s*(?P<n>\d+|[ivx]+|one|two|three|four|five|six)\b\W*(?:[:\-–].*)?$", re.I)
SET_RE = re.compile(r"^\W*(?:set\s*(?:[ivx]+|\d+|one|two|three)|encore\w*|\d+(?:st|nd|rd)\s+encore|soundcheck|sound check|pre-?show)\W*$", re.I)
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}

TRACK_RES = [
    # d1t01 / d1-01 / d1.01 / D1T01. Title
    re.compile(r"^\s*d(?P<disc>\d+)\s*[t\-._]\s*(?P<n>\d{1,3})\s*[.):\-\s]\s*(?P<t>\S.*)$", re.I),
    # 1-01 Title (disc-track)
    re.compile(r"^\s*(?P<disc>\d)-(?P<n>\d{2})\s*[.):\-\s]\s*(?P<t>[A-Za-z\"'(].*)$"),
    # [01] Title / (01) Title
    re.compile(r"^\s*[\[(](?P<n>\d{1,3})[\])]\s*(?P<t>\S.*)$"),
    # 01. Title / 01) / 01: / 01 - Title / 01 Title
    re.compile(r"^\s*(?P<n>\d{1,3})(?:\s*[.):]+|\s+-+(?=\s)|\s*-+(?=\s))\s*(?P<t>\S.*)$"),
    re.compile(r"^\s*(?P<n>\d{1,3})\s+(?P<t>[A-Za-z\"'(#\*~\+].*)$"),
    # t01 Title
    re.compile(r"^\s*t(?P<n>\d{1,3})\s*[.):\-\s]\s*(?P<t>[A-Za-z\"'(#\*~\+].*)$", re.I),
]
MARKS = r"\*~\+\^#@%=&!\$"
HASH_LINE = re.compile(
    r"[0-9a-f]{32}|\.(?:flac|shn|wav|ape|ffp|md5|st5)\b|^\s*;|\d+\.\d+\s{2,}\d{4,}|\(\d+ files\)", re.I)
LEGEND_RE = re.compile(r"^\s*(?P<m>[\*~\+\^#@%=&!\$]{1,3})\s*[-=:]?\s*(?P<text>[A-Za-z(].*)$")

ARTIST_HINT = re.compile(r"dave matthews|matthews|tim reynolds|d\s*&\s*t|dmb|band", re.I)
SOURCE_HINT = re.compile(r"sirius|\bsxm\b|soundboard|\bsbd\b|\bfm\b|webcast|livestream|stream", re.I)
VENUE_NOISE = re.compile(r"(?:^|\b)(?:taper|source|recorded|lineage|transfer|conversion|location|disc|set\b|total)", re.I)


# --------------------------------------------------------------------------- helpers

def _mk_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december"]


def _fix_month_typos(line: str) -> str:
    """'Spetember 6, 1996' -> 'September 6, 1996'. Only on lines with a number, and only for
    close misspellings (difflib ratio >= 0.85), so ordinary words such as 'Remember' are left alone."""
    if not re.search(r"\d", line):
        return line

    def fix(m: re.Match) -> str:
        w = m.group(0)
        if w.lower() in _MONTH_NAMES or len(w) < 5:
            return w
        best = difflib.get_close_matches(w.lower(), _MONTH_NAMES, n=1, cutoff=0.85)
        return best[0].capitalize() if best else w

    return re.sub(r"[A-Za-z]+", fix, line)


def parse_date(line: str) -> date | None:
    line = _fix_month_typos(line)
    for rx in _DATE_RES:
        m = rx.search(line)
        if not m:
            continue
        g = m.groupdict()
        y = int(g["y"])
        if y < 100:
            y += 2000 if y <= 30 else 1900
        if g.get("mon"):
            mo = MONTHS[g["mon"][:3].lower()]
            d = int(g["d"])
        else:
            mo, d = int(g["m"]), int(g["d"])
            if mo > 12 and d <= 12:
                mo, d = d, mo
        got = _mk_date(y, mo, d)
        if got:
            return got
    return None


def _is_date_only(line: str) -> bool:
    line = _fix_month_typos(line)
    for rx in _DATE_RES:
        m = rx.search(line)
        if m:
            rest = (line[:m.start()] + " " + line[m.end():])
            rest = re.sub(r"\(?\b(?:mon|tues?|wed|thu|fri|sat|sun)\w*\b\)?", "", rest, flags=re.I)
            if not re.search(r"[A-Za-z]{3,}", rest):
                return True
    return False


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("﻿", "")).strip()


def _decorative(line: str) -> bool:
    return not re.search(r"[A-Za-z0-9]", line)


def _is_label(line: str) -> tuple[str, str] | None:
    m = LABEL_RE.match(line)
    if not m:
        return None
    k = m.group("k").strip().lower()
    if k in LINEAGE_LABELS or k in OTHER_LABELS:
        return k, m.group("v").strip()
    return None


def _split_location(line: str) -> tuple[str | None, str, str | None, str | None] | None:
    """Try to read 'Venue - City, ST' / 'Venue, City, ST' / 'City, Country'.

    Returns (venue, city, region, country) or None.
    """
    s = _clean(line)
    if not s or len(s) > 120 or _is_label(s) or SOURCE_HINT.search(s) and "," not in s:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    # "Assago ITA" / "Worcester MA": split an upper-case trailing code off the last part
    m = re.match(r"^(?P<pre>.+?)\s+(?P<tail>[A-Z]{2,3})\.?$", parts[-1]) if parts else None
    if m and (region_code(m.group("tail")) or country_name(m.group("tail"))):
        parts[-1:] = [m.group("pre").strip(), m.group("tail")]
    if len(parts) < 2:
        return None
    region = country = None
    consumed = 0
    for tok in reversed(parts):
        if len(parts) - consumed <= 1:
            break
        tok_clean = re.sub(r"\s*\(.*?\)\s*", " ", tok).strip().rstrip(".")
        rc, cn = region_code(tok_clean), country_name(tok_clean)
        if rc:
            region = rc
            consumed += 1
            break
        if not cn:
            break
        consumed += 1
        if cn in ("USA", "Canada"):
            country = cn
            continue
        country = cn if (country is None or cn == "Ireland") else country
        if cn == "UK":
            continue
        # "Monterrey, NL, MEX": drop one trailing sub-division code before the city
        nxt = len(parts) - consumed - 1
        if nxt >= 1 and re.fullmatch(r"[A-Z]{2,3}", parts[nxt].strip()):
            consumed += 1
        break
    if region or country == "USA":
        country = None
    if consumed == 0:
        return None
    rest = parts[: len(parts) - consumed]
    if not rest:
        return None
    # A US state name may precede the code ("Albany, New York, NY") - ignore.
    city_part = rest[-1]
    venue_bits = rest[:-1]
    bits = re.split(r"\s+[-–]\s+", city_part)
    if len(bits) > 1:
        city_part = bits[-1]
        venue_bits.append(" - ".join(bits[:-1]))
    city_part = city_part.strip(" -–")
    # "Smirnoff Music Center Dallas"? cannot know; leave it.
    venue = ", ".join(venue_bits) or None
    return venue, city_part, region, country


def _parse_track_line(line: str) -> tuple[int | None, int, str] | None:
    if HASH_LINE.search(line) or _is_date_only(line):
        return None
    for rx in TRACK_RES:
        m = rx.match(line)
        if not m:
            continue
        title = m.group("t").strip()
        if not title or re.fullmatch(r"[\d:.\s\-]+", title):
            continue
        disc = int(m.group("disc")) if "disc" in rx.groupindex and m.group("disc") else None
        return disc, int(m.group("n")), title
    return None


def _clean_title(title: str) -> tuple[str, bool, str]:
    """Strip time, segue and footnote markers. Returns (title, segue, markers)."""
    t = re.sub(r"\s+", " ", title).strip()
    t = re.sub(r"^\[[^\]]{1,12}\]\s+(?=\S)", "", t)  # '[Set1] Satellite'
    t = re.sub(r"^E\s*:\s*(?=\S)", "", t)  # 'E: Two Step' encore prefix
    t = re.sub(r"[\ufffd\u00a5\u2022\u00b7]+\s*$", "", t).rstrip()
    segue = False
    marks = ""
    lead = re.match(r"^([\*~\+\^]+)\s*(?=\S)", t)
    if lead and not t.startswith("*" * 0 + "#"):
        marks += lead.group(1)
        t = t[lead.end():]
    for _ in range(6):
        before = t
        t = TIME_TAIL.sub("", t).rstrip()
        m = SEGUE_TAIL.search(t)
        if m and t[: m.start()].strip():
            segue = True
            t = t[: m.start()].rstrip()
        m = MARK_TAIL.search(t)
        if m and t[: m.start()].strip() and not re.search(r"#\d+$", t):
            marks += m.group(0).strip()
            t = t[: m.start()].rstrip()
        t = re.sub(r"\s*[-–—]\s*$", "", t)
        if t == before:
            break
    return t.strip(), segue, marks


def _disc_number(tok: str) -> int | None:
    tok = tok.lower()
    if tok.isdigit():
        return int(tok)
    return WORD_NUM.get(tok) or ROMAN.get(tok)


# Venues the owner tags by the venue's well-known town rather than the mailing address.
# (venue regex, region) -> city
VENUE_CITY_OVERRIDES = [
    (re.compile(r"\bgorge\b", re.I), "WA", "George"),
    (re.compile(r"alpine valley", re.I), "WI", "Elkhorn"),
    (re.compile(r"darien lake", re.I), "NY", "Darien Lake"),
    (re.compile(r"lake tahoe", re.I), "NV", "Lake Tahoe"),
]


def _apply_venue_overrides(info: ShowInfo) -> None:
    for rx, region, city in VENUE_CITY_OVERRIDES:
        if info.venue and info.region == region and rx.search(info.venue):
            info.city = city


# --------------------------------------------------------------------------- main

def parse_info_file(text: str) -> ShowInfo:
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"(?i)^(\s*(?:tapers?|source(?: info)?|location|conversion|lineage|transfer|equipment)) +- +", r"\1: ", l.rstrip("\\").rstrip())
             for l in text.split("\n")]
    info = ShowInfo()
    legend: dict[str, str] = {}

    # find where the setlist starts so the header scan stops there
    first_track = None
    for i, ln in enumerate(lines):
        if _parse_track_line(ln) and not _is_label(ln):
            first_track = i
            break
    header_end = first_track if first_track is not None else min(len(lines), 40)
    header = lines[: min(header_end, 40)]

    _parse_header(info, header)
    _apply_venue_overrides(info)
    _parse_lineage(info, lines, first_track)
    info.tracks = _parse_tracks(lines, first_track, legend)
    if not info.tracks:
        info.tracks = _parse_plain_setlist(lines, info)
    return info


def _parse_header(info: ShowInfo, header: list[str]) -> None:
    cand = [(_clean(l), l) for l in header if _clean(l) and not _decorative(l)]
    plain = [c for c, _ in cand if not _is_label(c)]

    # date: prefer a line that is only a date, then any line with a date
    for c in plain:
        if _is_date_only(c):
            info.date = parse_date(c)
            break
    if info.date is None:
        for c in plain:
            d = parse_date(c)
            if d and not re.search(r"\baired\b|\bposted\b|\buploaded\b", c, re.I):
                info.date = d
                break

    # location
    loc_idx = None
    for i, c in enumerate(plain):
        if _is_date_only(c) or ARTIST_HINT.fullmatch(c):
            continue
        r = _split_location(c)
        if r:
            venue, city, region, country = r
            info.city, info.region, info.country = city, region, country
            info.venue = venue
            loc_idx = i
            break
    if info.venue is None and loc_idx is not None:
        for j in range(loc_idx - 1, -1, -1):
            c = plain[j]
            if _is_date_only(c) or parse_date(c) or VENUE_NOISE.search(c) or j == 0 and ARTIST_HINT.search(c):
                continue
            info.venue = c
            break

    # artist: first plain line that is not the date/location
    for c in plain[:3]:
        if ARTIST_HINT.search(c) and not parse_date(c):
            info.artist = c
            break
    if info.artist is None and plain and not parse_date(plain[0]) and plain[0] != info.venue:
        if not _split_location(plain[0]):
            info.artist = plain[0]

    # No location at all (e.g. 2020 "Live From Home" broadcasts): the first remaining header
    # line names the show, which album_name uses in place of a city.
    if info.venue is None and loc_idx is None:
        for c in plain[:6]:
            if (c == info.artist or ARTIST_HINT.fullmatch(c) or parse_date(c) or VENUE_NOISE.search(c)
                    or re.search(r"(?i)\b(sirius|broadcast|radio|webcast|stream|source)\b", c)):
                continue
            info.venue = c
            break


def _parse_lineage(info: ShowInfo, lines: list[str], first_track: int | None) -> None:
    src_fallback = None
    for idx, raw in enumerate(lines):
        line = _clean(raw)
        if not line or _decorative(raw):
            continue
        lab = _is_label(line)
        if lab and lab[0] in LINEAGE_LABELS and lab[1]:
            info.lineage.append(f"{lab[0].title()}: {lab[1]}")
            if lab[0] in SOURCE_LABELS and info.source_line is None:
                info.source_line = lab[1]
            continue
        in_header = first_track is None or idx < first_track
        if in_header and idx < 30 and not lab and SOURCE_HINT.search(line) and len(line) < 200 \
                and not _parse_track_line(raw) and not ARTIST_HINT.fullmatch(line):
            info.lineage.append(line)
            src_fallback = src_fallback or line
    if info.source_line is None:
        info.source_line = src_fallback


def _parse_tracks(lines: list[str], first_track: int | None, legend: dict[str, str]) -> list[Track]:
    if first_track is None:
        return []
    tracks: list[Track] = []
    disc: int | None = None
    pending: list[tuple[Track, str]] = []
    for raw in lines[first_track - 3 if first_track >= 3 else 0:]:
        line = raw.strip()
        if not line:
            continue
        dm = DISC_RE.match(line)
        if dm:
            disc = _disc_number(dm.group("n")) or disc
            continue
        if SET_RE.match(line):
            continue
        tl = None if _is_label(line) else _parse_track_line(raw)
        if tl:
            d, n, title = tl
            title, segue, marks = _clean_title(title)
            if not title or _decorative(title):
                continue
            tr = Track(number=n, disc=d if d is not None else disc, title=title, segue=segue)
            tracks.append(tr)
            if marks:
                pending.append((tr, marks))
            continue
        lm = LEGEND_RE.match(line)
        if lm and not _is_label(line):
            legend[lm.group("m")[0]] = lm.group("text").strip()
    for tr, marks in pending:
        notes = [legend[c] for c in dict.fromkeys(marks) if c in legend]
        tr.note = "; ".join(notes) if notes else None
    return tracks


def _title_like(line: str) -> bool:
    s = line.strip()
    return bool(s) and not _decorative(s) and len(s) <= 70 and not _is_label(s) and not HASH_LINE.search(s) \
        and not LEGEND_RE.match(s) and not _is_date_only(s) and not re.match(r"(?i)^total\b", s)


def _parse_plain_setlist(lines: list[str], info: ShowInfo) -> list[Track]:
    """Setlists without numbers: the longest run of short non-label lines (single blank lines allowed)."""
    skip = {x for x in (info.venue, info.artist) if x}
    runs: list[list[str]] = []
    cur: list[str] = []
    blanks = 0
    for raw in lines:
        s = _clean(raw)
        if not s:
            blanks += 1
            if blanks > 1 and cur:
                runs.append(cur)
                cur = []
            continue
        blanks = 0
        if DISC_RE.match(s) or SET_RE.match(s) or re.match(r"(?i)^(?:set\s*list|setlist)\s*:?$", s):
            continue
        if re.fullmatch(r"[-=*_~.\s]{3,}", raw.strip()):  # '---------' divider before the encore
            continue
        if (not _title_like(s) or s in skip or _split_location(s) or parse_date(s) or len(s) > 70
                or re.match(r"(?i)^(?:notes?|comments?|thanks|support|please|enjoy)\b\s*[-:!]?", s)):
            if cur:
                runs.append(cur)
            cur = []
            continue
        cur.append(raw)
    if cur:
        runs.append(cur)
    runs = [r for r in runs if len(r) >= 8]
    if not runs:
        return []
    best = max(runs, key=len)
    tracks: list[Track] = []
    for raw in best:
        title, segue, marks = _clean_title(_clean(raw))
        if title:
            tracks.append(Track(number=len(tracks) + 1, disc=None, title=title, segue=segue))
    return tracks
