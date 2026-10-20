"""Parse torrent/release names like 'dmb2025-07-12.ca-11.flac16' or 'd+t1996.02.16.siriusxm.web-dl.flac16'.

Handles the compact release names (dmb2025-07-12.ca-11.flac16, dmb2023-03-19_dpa4061_flac16,
d&t2022-07-17ck61.gf, dmb20240424.flac24, dmb1991-1022 ...), site display names with extra text
('dmb1998-07-25.cmc64 - Camden (2026 Remix)', 'DMB - 2022/08/20 West Palm Beach - dmb2022-08-20.mk4.flac24')
and the hand-named library folders ('2022.11.18 - Madison Square Garden, New York, NY').

Partial dates ('dmb1991-07-xx', 'dm1989-xx-xx') give ``date=None``.
"""
from __future__ import annotations

import re
from datetime import date

from .models import TorrentName

# artist code prefix -> normalised code
_CODES = {"dmb": "dmb", "dm": "dm", "dt": "d&t", "d&t": "d&t", "d+t": "d&t", "dm&bt": "other", "dm+bt": "other"}
_CODE_RE = re.compile(
    r"(?<![a-z0-9])(?P<code>dm[&+]bt|d[+&]t|dmb|dt|dm)(?:(?=\d{4})|(?<=[+&]t)(?=\.))", re.I)
_ARTIST_TEXT_RE = re.compile(
    r"dave\s+matthews(?:\s*(?:&|and|\+)\s*(?:tim\s+reynolds|boyd\s+tinsley|friends))?(?:\s+band)?|dmb|dave\s+and\s+tim", re.I)

_SEP = r"[-./_]?"
_DATE_YMD = re.compile(rf"(?<!\d)(\d{{4}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})(?!\d|[-/]\d)")
_DATE_MDY = re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})[-./](\d{4})(?!\d)")
_DATE_MDY2 = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{2})(?!\d)")

_FORMATS = {"flac16": "flac16", "flac24": "flac24", "flac": "flac", "shn": "shn", "shnf": "shn",
            "mp3": "mp3", "wav": "wav", "ape": "ape", "m4a": "m4a"}
_FMT_PRIORITY = ("flac16", "flac24", "shn", "flac", "mp3", "wav", "ape", "m4a")
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _valid(y: int, m: int, d: int) -> date | None:
    if not 1960 <= y <= 2100:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _find_date(text: str) -> tuple[date, re.Match] | None:
    for m in _DATE_YMD.finditer(text):
        d = _valid(int(m[1]), int(m[2]), int(m[3]))
        if d:
            return d, m
    for m in _DATE_MDY.finditer(text):
        d = _valid(int(m[3]), int(m[1]), int(m[2]))
        if d:
            return d, m
    for m in _DATE_MDY2.finditer(text):
        d = _valid(2000 + int(m[3]), int(m[1]), int(m[2]))
        if d:
            return d, m
    return None


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)   # (set I), (2026 Remix)
    text = text.split(" - ")[0]                          # ' - Camden' venue/city suffixes
    text = re.sub(r"\d{4}[-./_](?:\d{2}|xx)[-./_]xx|xx[-./_]xx", " ", text, flags=re.I)  # partial dates
    out = []
    for t in _TOKEN_RE.findall(text.lower()):
        if re.fullmatch(r"\d{4}", t) or t in out:       # bare years add nothing
            continue
        out.append(t)
    return out


def _pick_format(tokens: list[str]) -> tuple[str | None, list[str]]:
    found = [_FORMATS[t] for t in tokens if t in _FORMATS]
    rest = [t for t in tokens if t not in _FORMATS]
    if not found:
        return None, rest
    fmt = next(f for f in _FMT_PRIORITY if f in found)
    if fmt == "flac":
        if "16bit" in rest:
            fmt = "flac16"
        elif "24bit" in rest:
            fmt = "flac24"
    return fmt, rest


def parse_torrent_name(raw: str) -> TorrentName:
    s = raw.strip()
    code_m = _CODE_RE.search(s)
    if code_m:
        code = _CODES[code_m["code"].lower()]
        core = s[code_m.end():]
        found = _find_date(core)
        if found:
            when, dm = found
            tail = core[dm.end():] if dm.start() < 3 else core[:dm.start()] + " " + core[dm.end():]
        else:
            when, tail = None, core
            fallback = _find_date(s[:code_m.start()])   # date written before the release name
            if fallback:
                when = fallback[0]
    else:
        code = "other"
        stripped = _ARTIST_TEXT_RE.sub(" ", s)
        if re.search(r"dave\s+matthews\s+band|(?<![a-z])dmb(?![a-z])", s, re.I):
            code = "dmb"
        elif re.search(r"dave\s+matthews", s, re.I):
            code = "dm"
        found = _find_date(stripped)
        if found:
            when, dm = found
            tail = stripped[:dm.start()] + " " + stripped[dm.end():]
        else:
            when, tail = None, stripped
    fmt, tokens = _pick_format(_tokens(tail))
    return TorrentName(raw=raw, artist_code=code, date=when, source_tokens=tokens, fmt=fmt)
