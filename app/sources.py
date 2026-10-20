"""Classify the recording source and return the album suffix.

Returns '' for audience/taper recordings, otherwise one of: 'siriusxm', 'sbd', 'fm', 'webcast', 'matrix', 'tv'.

Order: torrent-name tokens first, then the info file's Source line (and the rest of the lineage).
A taper microphone mixed with SiriusXM (e.g. 'ck61+siriusxm') is a taper recording, so no suffix.
The result is always lowercase, whatever case the token or the author's older tags used ('SBD').
"""
from __future__ import annotations

import re

from .models import ShowInfo, TorrentName

SUFFIXES = ("siriusxm", "sbd", "fm", "webcast", "matrix", "tv")

# normalised torrent-name token -> suffix
TOKEN_MAP = {
    "siriusxm": "siriusxm", "sirius": "siriusxm", "sxm": "siriusxm", "xm": "siriusxm",
    "sbd": "sbd", "asbd": "sbd", "dsbd": "sbd", "fobsbd": "sbd", "soundboard": "sbd",
    "fm": "fm", "fmbroadcast": "fm",
    "webcast": "webcast", "webdl": "webcast", "livestream": "webcast",
    "youtube": "webcast", "webrip": "webcast", "web": "webcast", "stream": "webcast",
    "tv": "tv", "hdtv": "tv", "pbs": "tv", "conan": "tv", "letterman": "tv", "fallon": "tv",
    "kimmel": "tv", "colbert": "tv", "snl": "tv", "tvrip": "tv",
    "matrix": "matrix", "mtx": "matrix",
}

_SIRIUS_RE = re.compile(r"sirius|\bsxm\b|\bsxm[-_ ]|\bxm\b|satellite radio", re.I)
_SBD_RE = re.compile(r"\bsbd\b|\basbd\b|\bdsbd\b|soundboard|sound board|\bboard (?:feed|tape|recording)\b|\bdesk (?:feed|tape)\b|\bfob\b feed", re.I)
_FM_RE = re.compile(r"\bfm\b|fm broadcast|radio broadcast|\bfm radio\b", re.I)
_WEB_RE = re.compile(r"web-?cast|live ?stream|\bstream(?:ed|ing)?\b|web-?dl|\bhls\b|pay[- ]per[- ]view|\bppv\b|\bnugs", re.I)
_MATRIX_RE = re.compile(r"\bmatrix\b|\bmtx\b", re.I)
_MIC_RE = re.compile(
    r"\bak(?:g|c)\b|\bakg|\bck-?\d|\bc-?414|\bschoeps|\bmk-?\d|\bccm|\bdpa\b|\bdpa-?\d|\b40\d\d\b|\bneumann|\bkm-?1\d\d|"
    r"\bca-?1\d|\bcmc|\bnbox|\bsonosax|\bsound ?devices|\bsounddevices|\bsennheiser|\bmxl|\baudio[- ]technica|\bat-?\d{3}|"
    r"\bat\d{3}|\bst-?\d{3}|\bst9100|\bsoundfield|\bcore ?sound|\bmicrophone|\bmics?\b|\bomni|\bcardioid|\bnaiant|"
    r"\bzoom\b|\btascam|\bedirol|\bsony (?:d|pcm|tc)|\bbeyer|\bsanken|\bearthworks|\bmk\d",
    re.I,
)


def _norm_token(tok: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tok.lower())


def _name_kinds(name: TorrentName) -> list[str]:
    """Suffix kinds found in the torrent-name tokens (and the raw name), in encounter order."""
    toks: list[str] = []
    for t in name.source_tokens or []:
        toks.append(t)
        toks.extend(p for p in re.split(r"[-+_.\s]+", t) if p)
    # tolerate source_tokens that were not extracted, e.g. 'ck61+siriusxm' or 'sirius-xm'
    if name.raw:
        toks.extend(p for p in re.split(r"[.+_\s]+", name.raw) if p)
        for chunk in re.split(r"[.+_\s]+", name.raw):
            toks.extend(p for p in chunk.split("-") if p)
    kinds: list[str] = []
    for t in toks:
        n = _norm_token(t)
        k = TOKEN_MAP.get(n)
        if k and k not in kinds:
            kinds.append(k)
    # 'web-dl' on its own means a webcast capture; with sirius it is the SiriusXM stream
    if "siriusxm" in kinds and "webcast" in kinds:
        kinds.remove("webcast")
    return kinds


def _info_text(info: ShowInfo) -> str:
    parts = [info.source_line or ""]
    parts += info.lineage or []
    return "\n".join(p for p in parts if p)


def _info_kind(info: ShowInfo) -> str:
    """'' for a taper/audience mic, else a suffix, judged from Source and lineage lines."""
    text = _info_text(info)
    if not text.strip():
        return ""
    if _SIRIUS_RE.search(text):
        return "" if _mic_taper_with_sirius(text) else "siriusxm"
    if _MATRIX_RE.search(text):
        return "matrix"
    if _is_sbd_mic_mix(info.source_line or ""):
        return "matrix"  # soundboard mixed with audience mics
    if _SBD_RE.search(text) and not _MIC_RE.search(info.source_line or ""):
        return "sbd"
    if _FM_RE.search(text) and not _MIC_RE.search(info.source_line or ""):
        return "fm"
    if re.search(r"web-?cast|live ?stream|web-?dl|web-?rip|youtube|\bhls\b", text, re.I) and not _MIC_RE.search(info.source_line or ""):
        return "webcast"
    if re.search(r"\b(?:hd)?tv\b|television|\bpbs\b|broadcast on [A-Z]{3}\b", text, re.I) and not _MIC_RE.search(info.source_line or ""):
        return "tv"
    return ""


def _is_sbd_mic_mix(source_line: str) -> bool:
    """'SBD & Schoeps MK-4V onstage mics > DAT': first stage joins a soundboard feed and mics."""
    first = re.split(r">", source_line, maxsplit=1)[0]
    return bool(_SBD_RE.search(first) and _MIC_RE.search(first) and re.search(r"\+|&|\band\b|\bwith\b", first))


def _mic_taper_with_sirius(text: str) -> bool:
    """True when a Source/Lineage line names a microphone as well as SiriusXM (a taper mix)."""
    src_lines = [l for l in text.split("\n") if re.match(r"(?i)\s*(source|source info|sources|lineage|equipment)\s*:", l)]
    for l in src_lines:
        if _MIC_RE.search(l):
            return True
    return False


def source_suffix(name: TorrentName, info: ShowInfo) -> str:
    kinds = _name_kinds(name)
    ik = _info_kind(info)
    if kinds:
        k = kinds[0]
        if k == "siriusxm" and _taper_plus_sirius(name, info):
            return ""
        return k
    return ik


def _taper_plus_sirius(name: TorrentName, info: ShowInfo) -> bool:
    """e.g. 'dmb2023-06-02.ck61+siriusxm': a mic joined to SiriusXM, and the info file does not
    describe the recording as the SiriusXM stream."""
    raw = name.raw.lower()
    if re.search(r"\+\s*(?:sirius|sxm)|(?:sirius|sxm)[a-z-]*\s*\+", raw):
        return not _pure_sirius_info(info)
    return _mic_taper_with_sirius(_info_text(info))


def _pure_sirius_info(info: ShowInfo) -> bool:
    """The info file calls the recording a SiriusXM capture and names no microphone."""
    text = _info_text(info)
    return bool(_SIRIUS_RE.search(text)) and not _mic_taper_with_sirius(text)
