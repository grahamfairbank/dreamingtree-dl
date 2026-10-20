"""Info-file parser tests: inline layouts plus the regression corpus (hand-tagged DMB_Live shows).

The corpus lives in tests/fixtures/info/: one `<folder>.txt` per show and `expected.json`
({folder: {"album": <owner's ALBUM tag>, "info": "<folder>.txt"}}).
Run with UPDATE_MISMATCHES=1 to regenerate tests/fixtures/info/MISMATCHES.md.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from app.albumname import album_name
from app.infofile import parse_date, parse_info_file
from app.models import ShowInfo, TorrentName
from app.sources import source_suffix

FIX = Path(__file__).parent / "fixtures" / "info"
MISMATCH_MD = FIX / "MISMATCHES.md"

# The rates achieved when this test was written (see MISMATCHES.md). The tests fail if they drop.
MIN_STRICT_RATE = 0.765      # exact string match (trailing whitespace ignored)  (211/275 after youtube -> webcast; owner tagged one without suffix)
MIN_ADJUSTED_RATE = 0.905    # exact + accepted known mismatches (owner conventions we do not copy)

SUFFIXES = ("siriusxm", "sbd", "fm", "webcast", "matrix")

# Mismatches where the info file is wrong or the author's tag is a one-off error (owner is not the reference).
MANUAL_CATEGORY = {
    "dmb2022-05-18.mk4.babynbox.flac16": "info file error (wrong state)",
    "dmb2021.9.22.flac.gf": "info file error (city typo)",
    "dmb1998-12-08.akg451.shnf": "info file error (city typo)",
    "dmb1998-12-13.ms957.shnf": "owner/info disagree (different city)",
    "dmb2021-08-14.ca-11.flac16": "owner typo",
    "dmb2008-08-19.siriusxm.web-dl.flac16": "owner typo",
    "dmb2021-10-08.akgc414xlii.flac16": "owner tagged the metro area (Denver) not the venue town",
    "dmb2023-06-14ck63.flac16": "owner picked a different town than the info file",
    "dmb2011-09-18": "apostrophe dropped by owner",
}

# Known mismatches we accept: the author's tag is a venue nickname, has extra free text, differs only in
# punctuation, or is a plain data error (typo / wrong date) on the author's or the info file's side.
# Convention disagreements (country codes, suffix omitted, town choice) are NOT accepted.
ACCEPTED = {
    "venue/nickname album (author used a venue name instead of a city)",
    "extra text appended by owner",
    "owner punctuation/whitespace",
    "owner date typo (differs from folder name and info)",
    "owner typo",
    "apostrophe dropped by owner",
    "info file error (wrong state)",
    "info file error (city typo)",
}


def folder_name_to_torrent(folder: str, info: ShowInfo) -> TorrentName:
    m = re.search(r"(\d{4})[-.]?(\d\d)[-.]?(\d\d)", folder)
    d = None
    if m:
        try:
            d = date(*map(int, m.groups()))
        except ValueError:
            d = None
    d = d or info.date
    toks = [t for t in re.split(r"[.\s_]+", folder.lower()) if t]
    return TorrentName(folder, "dmb", d, toks, None)


def build_album(folder: str, text: str) -> str | None:
    info = parse_info_file(text)
    name = folder_name_to_torrent(folder, info)
    if not name.date:
        return None
    return album_name(name.date, info.city, info.region, info.country, source_suffix(name, info))


def split_album(a: str) -> tuple[str, str, str]:
    """-> (date, place, suffix)"""
    a = a.strip()
    m = re.match(r"(\d{4}-\d\d-\d\d)\s*[:\-]?\s*(.*)$", a)
    d, rest = (m.group(1), m.group(2)) if m else ("", a)
    suf = ""
    m2 = re.search(r"\s(\S+)$", rest)
    if m2 and m2.group(1).lower() in SUFFIXES + ("volta", "soulshine", "sirisuxm"):
        suf, rest = m2.group(1), rest[: m2.start()]
    return d, rest.strip(), suf


def classify(folder: str, exp: str, got: str | None) -> str:
    if folder in MANUAL_CATEGORY:
        return MANUAL_CATEGORY[folder]
    if got is None:
        return "no date available"
    ed, ep, es = split_album(exp)
    gd, gp, gs = split_album(got)
    if ed != gd:
        return "owner date typo (differs from folder name and info)"
    if ep == gp:
        if not es and gs:
            return "owner omitted suffix"
        if es and not gs:
            return "owner suffix not derivable (SBD/other label)" if es.lower() not in SUFFIXES else "source not detected"
        if es.lower() != gs:
            return "owner custom suffix (volta/soulshine/typo)"
    if re.sub(r"[\s:,\-]+", " ", exp).strip() == re.sub(r"[\s:,\-]+", " ", got).strip():
        return "owner punctuation/whitespace"
    if ep.startswith(gp) and len(ep) > len(gp):
        return "extra text appended by owner"
    if not re.search(r",\s*(?:[A-Z]{2}|UK|[A-Z][a-z]+(?: [A-Z][a-z]+)*)$", ep) or ep.upper().endswith(" SBD"):
        return "venue/nickname album (author used a venue name instead of a city)"
    if re.search(r",\s*(?:[A-Z]{2,3})$", ep) and not re.search(r",\s*[A-Z]{2}$", gp):
        return "author used a country code (NL, CZ, MX, DEU, CA)"
    if ep.split(",")[-1].strip() == gp.split(",")[-1].strip():
        return "owner picked a different city name (spelling or neighbouring town)"
    return "other"


def evaluate() -> list[dict]:
    exp = json.loads((FIX / "expected.json").read_text(encoding="utf8"))
    out = []
    for folder, rec in sorted(exp.items()):
        text = (FIX / rec["info"]).read_text(encoding="utf8", errors="replace")
        got = build_album(folder, text)
        want = rec["album"].rstrip()
        ok = got == want
        out.append({"folder": folder, "expected": want, "got": got, "ok": ok,
                    "category": None if ok else classify(folder, want, got)})
    return out


def rates(results: list[dict]) -> tuple[float, float]:
    n = len(results)
    strict = sum(r["ok"] for r in results) / n
    adj = sum(r["ok"] or r["category"] in ACCEPTED for r in results) / n
    return strict, adj


def render_mismatches(results: list[dict]) -> str:
    strict, adj = rates(results)
    n = len(results)
    bad = [r for r in results if not r["ok"]]
    cats: dict[str, list[dict]] = {}
    for r in bad:
        cats.setdefault(r["category"], []).append(r)
    lines = [
        "# Regression corpus mismatches",
        "",
        "Generated by `UPDATE_MISMATCHES=1 scripts/test.sh tests/test_infofile.py`. Do not edit by hand.",
        "",
        f"- Shows in corpus: {n}",
        f"- Exact matches: {n - len(bad)} ({strict:.1%})",
        f"- Exact + accepted known mismatches: {sum(r['ok'] or r['category'] in ACCEPTED for r in results)} ({adj:.1%})",
        "- Accepted categories: " + "; ".join(sorted(ACCEPTED)),
        "- The album is built from the torrent/folder name date (falls back to the info-file date), the info file, "
        "and `source_suffix`; the author's tag is the expected value.",
        "",
        "## Categories",
        "",
        "| Category | Count | Accepted |",
        "|---|---|---|",
    ]
    for c, rs in sorted(cats.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"| {c} | {len(rs)} | {'yes' if c in ACCEPTED else 'no'} |")
    lines += ["", "## Mismatches", "", "| Folder | Owner's ALBUM | Generated | Category |", "|---|---|---|---|"]
    for r in sorted(bad, key=lambda r: (r["category"], r["folder"])):
        esc = lambda s: (s or "").replace("|", "\\|")
        lines.append(f"| {esc(r['folder'])} | {esc(r['expected'])} | {esc(r['got'])} | {r['category']} |")
    return "\n".join(lines) + "\n"


def test_corpus_regression_rate():
    results = evaluate()
    strict, adj = rates(results)
    if os.environ.get("UPDATE_MISMATCHES"):
        MISMATCH_MD.write_text(render_mismatches(results), encoding="utf8")
    assert len(results) >= 250
    assert strict >= MIN_STRICT_RATE, f"exact match rate fell to {strict:.3f}"
    assert adj >= MIN_ADJUSTED_RATE, f"adjusted match rate fell to {adj:.3f}"


def test_mismatches_file_is_current():
    if os.environ.get("UPDATE_MISMATCHES"):
        return
    assert MISMATCH_MD.read_text(encoding="utf8") == render_mismatches(evaluate()), \
        "MISMATCHES.md is stale; run UPDATE_MISMATCHES=1 scripts/test.sh tests/test_infofile.py"


def test_corpus_dates_parse():
    exp = json.loads((FIX / "expected.json").read_text(encoding="utf8"))
    missing = [k for k, v in exp.items()
               if parse_info_file((FIX / v["info"]).read_text(encoding="utf8", errors="replace")).date is None]
    assert not missing


# ---------------------------------------------------------------- inline layouts

def test_basic_layout_artist_date_venue_city():
    t = "Dave Matthews Band\nJuly 9, 2022\nSaratoga Performing Arts Center\nSaratoga Springs, NY\n\nTaper: Bob\nSource: ck63 > 722\n\n01. Intro  01:00\n02. Song One >  07:50\n"
    i = parse_info_file(t)
    assert (i.artist, i.venue, i.city, i.region, i.country) == (
        "Dave Matthews Band", "Saratoga Performing Arts Center", "Saratoga Springs", "NY", None)
    assert i.date == date(2022, 7, 9)
    assert i.source_line == "ck63 > 722"
    assert any(l.startswith("Taper:") for l in i.lineage)
    assert [(t.number, t.title, t.segue) for t in i.tracks] == [(1, "Intro", False), (2, "Song One", True)]


def test_city_state_before_date_and_venue_dash_city():
    t = "Dave Matthews Band\nSmirnoff Music Center - Dallas, TX\nAugust 19, 2006\n"
    i = parse_info_file(t)
    assert (i.city, i.region, i.venue) == ("Dallas", "TX", "Smirnoff Music Center")
    assert i.date == date(2006, 8, 19)


def test_full_state_name_and_canada():
    i = parse_info_file("Dave Matthews Band\nRoyal Albert\nToronto, Ontario\n5/24/23\n")
    assert (i.city, i.region, i.country) == ("Toronto", "ON", None)
    i = parse_info_file("DMB\nMolson Amphitheatre - Toronto, ON, CAN\n9 June 2009\n")
    assert (i.city, i.region, i.country) == ("Toronto", "ON", None)
    assert i.date == date(2009, 6, 9)


def test_uk_and_foreign_country():
    i = parse_info_file("Dave Matthews Band\nApril 24, 2024\nRoyal Albert Hall\nLondon, England, UK\n")
    assert (i.city, i.region, i.country) == ("London", None, "UK")
    i = parse_info_file("Dave Matthews Band\nMay 2, 2024\nAccor Arena\nParis, France\n")
    assert (i.city, i.region, i.country) == ("Paris", None, "France")
    i = parse_info_file("Dave Matthews Band\n2024-04-19\nMediolanum Forum, Assago ITA\n")
    assert (i.city, i.country) == ("Assago", "Italy")


def test_date_formats():
    cases = {
        "July 21, 1992": date(1992, 7, 21), "Sept 18th, 2010": date(2010, 9, 18),
        "May 20th 2022": date(2022, 5, 20), "17 November 2023": date(2023, 11, 17),
        "Wednesday, May 1, 2024": date(2024, 5, 1), "07-04-2015": date(2015, 7, 4),
        "5/24/23": date(2023, 5, 24), "12.29.92": date(1992, 12, 29),
        "1992-07-21": date(1992, 7, 21), "1993.05.27": date(1993, 5, 27), "10-11-21": date(2021, 10, 11),
    }
    for text, want in cases.items():
        assert parse_date(text) == want, text


def test_setlist_discs_footnotes_and_legend():
    t = ("Dave Matthews Band\nJune 15, 1997\nNissan Pavilion\nBristow, VA\n\nDisc 1\n"
         "01. Intro   00:56\n02. Two Step  08:12\n03. Crash Into Me *  06:57\n\n"
         "Disc Two:\nd2t01 Seek Up ~ -> 16:01\n02. Ants Marching (Snare intro) > 10:05\n\n"
         "* with Tim Reynolds\n~ first time played\n")
    i = parse_info_file(t)
    assert [(x.disc, x.number, x.title, x.segue) for x in i.tracks] == [
        (1, 1, "Intro", False), (1, 2, "Two Step", False), (1, 3, "Crash Into Me", False),
        (2, 1, "Seek Up", True), (2, 2, "Ants Marching (Snare intro)", True)]
    assert i.tracks[2].note == "with Tim Reynolds"
    assert i.tracks[3].note == "first time played"


def test_dxtxx_and_hash_lines_ignored():
    t = ("Dave Matthews Band\n1999-07-03\nVenue\nCity, NJ\n\n"
         "d1t01 One\nd1t02 Two >\nd2t01 Three\n\n"
         "a1b2c3d4e5f60718293a4b5c6d7e8f90  d1t01.flac\n")
    i = parse_info_file(t)
    assert [(x.disc, x.number, x.title) for x in i.tracks] == [(1, 1, "One"), (1, 2, "Two"), (2, 1, "Three")]
    assert i.tracks[1].segue


def test_unnumbered_setlist():
    songs = ["Song %d" % n for n in range(1, 12)]
    t = "Dave Matthews Band\nMay 1, 2024\nVenue\nMiami, FL\n\nTaper: X\n\nSetlist:\n" + "\n".join(songs) + "\n"
    i = parse_info_file(t)
    assert [x.title for x in i.tracks] == songs


def test_lineage_collected_and_sirius_fallback():
    t = "Dave Matthews Band\nJune 22, 2019\nXfinity Theatre\nHartford, CT\nAired on SiriusXM (Channel 30) on September 27, 2019.\nTransfer: HLS > ffmpeg\n"
    i = parse_info_file(t)
    assert i.date == date(2019, 6, 22)
    assert "SiriusXM" in (i.source_line or "")
    assert any(l.startswith("Transfer:") for l in i.lineage)


def test_garbage_input_does_not_raise():
    assert parse_info_file("").tracks == []
    i = parse_info_file("\x00\x01 nothing here\n")
    assert i.date is None


def test_downloaded_sample_info_files_parse():
    """Info files from real sample torrents (tests/fixtures/samples/<name>/*.txt)."""
    from app.pipeline import Pipeline

    # Pick each sample's info file the way the pipeline does; FILE_LISTING.txt is an
    # `ls -l` capture, not part of the torrent.
    samples = []
    for d in (Path(__file__).parent / "fixtures" / "samples").iterdir():
        if d.is_dir():
            files = [f for f in d.iterdir() if f.name != "FILE_LISTING.txt"]
            if (p := Pipeline.find_info_file(None, files, d.name)) is not None:
                samples.append(p)
    for p in samples:
        i = parse_info_file(p.read_text(encoding="utf8", errors="replace"))
        assert i.date is not None, p
        assert i.city or i.country or i.venue, p  # no-location broadcasts name the show instead


def test_no_location_broadcast_uses_show_title():
    from app.albumname import album_name
    text = ("Dave Matthews\nJuly 03, 2020\nLive From Home By Request\nSirius XM Live Broadcast\n\n"
            "Source: SiriusXM hls stream\n\n01. Intro\n02. Cry Freedom\n")
    i = parse_info_file(text)
    assert i.city is None and i.venue == "Live From Home By Request"
    assert album_name(i.date, i.city, i.region, i.country, "siriusxm", fallback=i.venue) == \
        "2020-07-03: Live From Home By Request siriusxm"


def test_month_typos_are_tolerated():
    from app.infofile import parse_date
    assert parse_date("Spetember 6, 1996") == date(1996, 9, 6)
    assert parse_date("Febuary 2, 1999") == date(1999, 2, 2)
    assert parse_date("Remember 2 Things 1993") is None or parse_date("Remember 2 Things 1993").month != 12


def test_plain_setlist_continues_past_dashed_encore_divider():
    p = Path(__file__).parent / "fixtures" / "samples" / "dmb2025-05-27ck63.flac16 copy" / "dmb2025-05-27ck63.txt"
    i = parse_info_file(p.read_text(encoding="utf8", errors="replace"))
    assert len(i.tracks) == 21
    assert i.tracks[-1].title == "Shake Me Like A Monkey"
    assert i.city == "Syracuse" and i.region == "NY"
