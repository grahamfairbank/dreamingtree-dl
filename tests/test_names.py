from pathlib import Path

import pytest

from app.names import parse_torrent_name as parse

FIX = Path(__file__).parent / "fixtures" / "names"


@pytest.mark.parametrize("raw,code,ymd,tokens,fmt", [
    ("dmb2025-07-12.ca-11.flac16", "dmb", (2025, 7, 12), ["ca-11"], "flac16"),
    ("dmb2023-03-19_dpa4061_flac16", "dmb", (2023, 3, 19), ["dpa4061"], "flac16"),
    ("d+t1996.02.16.siriusxm.web-dl.flac16", "d&t", (1996, 2, 16), ["siriusxm", "web-dl"], "flac16"),
    ("d&t2022-07-17ck61.gf", "d&t", (2022, 7, 17), ["ck61", "gf"], None),
    ("dm2020-03-26.siriusxm.web-dl.flac16", "dm", (2020, 3, 26), ["siriusxm", "web-dl"], "flac16"),
    ("dmb2001-07-14.sbd.shnf", "dmb", (2001, 7, 14), ["sbd"], "shn"),
    ("dmb2024-05-24.dpa4011+siriusxm.flac16", "dmb", (2024, 5, 24), ["dpa4011", "siriusxm"], "flac16"),
    ("dmb20240424.flac24", "dmb", (2024, 4, 24), [], "flac24"),
    ("dmb2021.9.22.flac.gf", "dmb", (2021, 9, 22), ["gf"], "flac"),
    ("dmb1991-1022", "dmb", (1991, 10, 22), [], None),
    ("dt1993-04-22", "d&t", (1993, 4, 22), [], None),
    ("dmb2019-06.19.mk012.dpa4061.flac24", "dmb", (2019, 6, 19), ["mk012", "dpa4061"], "flac24"),
    ("dmb2022-06-21mk4.flac16", "dmb", (2022, 6, 21), ["mk4"], "flac16"),
])
def test_release_names(raw, code, ymd, tokens, fmt):
    n = parse(raw)
    assert (n.artist_code, (n.date.year, n.date.month, n.date.day), n.source_tokens, n.fmt) == (code, ymd, tokens, fmt)
    assert n.raw == raw


@pytest.mark.parametrize("raw,code,ymd,tokens,fmt", [
    # site display names carry extra text
    ("dmb1998-07-25.cmc64 - Camden (2026 Remix)", "dmb", (1998, 7, 25), ["cmc64"], None),
    ("DMB - 2022/08/20 West Palm Beach - dmb2022-08-20.mk4.babynbox.flac24", "dmb", (2022, 8, 20),
     ["mk4", "babynbox"], "flac24"),
    ("dmb2024-06-14.siriusxm.flac24 (complete)", "dmb", (2024, 6, 14), ["siriusxm"], "flac24"),
    ("dmb1991-08-21.asbd.reconvert.flac16 **New Source**", "dmb", (1991, 8, 21),
     ["asbd", "reconvert", "new", "source"], "flac16"),
    ("dmb2019-03-22.Mehr_Theater.Hamburg.srg.44KHz.16Bit.flac+mp3", "dmb", (2019, 3, 22),
     ["mehr", "theater", "hamburg", "srg", "44khz", "16bit"], "flac16"),
    ("dt2017-03-26.AFAS_live.Amsterdam.srg.44KHz.16Bit.flac+mp3", "d&t", (2017, 3, 26),
     ["afas", "live", "amsterdam", "srg", "44khz", "16bit"], "flac16"),
    ("dm1999-06-15.Q101.5.shnf", "dm", (1999, 6, 15), ["q101", "5"], "shn"),
    # library folder styles
    ("2022.11.18 - Madison Square Garden, New York, NY", "other", (2022, 11, 18), [], None),
    ("Dave Matthews Band SiriusXM 2024-05-24 - West Palm Beach, Florida", "dmb", (2024, 5, 24), ["siriusxm"], None),
    ("2023-06-23 SiriusXM - The Pavilion at Star Lake, Burgettstown, PA", "other", (2023, 6, 23), ["siriusxm"], None),
    ("07-15-1999 Portland Dead Aunt Thelma's Studio Dave Solo", "other", (1999, 7, 15),
     ["portland", "dead", "aunt", "thelma", "s", "studio", "dave", "solo"], None),
])
def test_display_and_library_names(raw, code, ymd, tokens, fmt):
    n = parse(raw)
    assert (n.artist_code, (n.date.year, n.date.month, n.date.day), n.source_tokens, n.fmt) == (code, ymd, tokens, fmt)


@pytest.mark.parametrize("raw,code", [
    ("dm&bt1995-04-14", "other"), ("dm+bt1995-04-14.aud.shnf", "other"),
    ("agr1997-03-18.dsbd-reconvert - Charlottesville (Dave guesting)", "other"),
    ("jwelles2026-09-06.cmc6.mk4.flac16", "other"),
    ("d&t.farm.aid.2024.siriusxm.flac16", "d&t"),
    ("soulshinebenefit.2024-11-24.siriusxm.flac16", "other"),
])
def test_artist_codes(raw, code):
    assert parse(raw).artist_code == code


@pytest.mark.parametrize("raw", ["dmb1991-07-xx.flac16", "dm1989-xx-xx_Mothers Night Tape",
                                 "d&t.farm.aid.2024.siriusxm.flac16", "Forever Moore - The Best of LeRoi Moore",
                                 "dmb20224-04-14.siriusxm.flac16", "", "stage"])
def test_no_full_date(raw):
    n = parse(raw)
    assert n.date is None
    assert n.raw == raw


def test_partial_date_not_in_tokens():
    assert parse("dmb1991-07-xx.flac16").source_tokens == []


def test_invalid_calendar_date_is_rejected():
    assert parse("dmb2023-02-30.ck63.flac16").date is None


def test_library_corpus_parses_with_dates():
    names = [n for n in (FIX / "library_names.txt").read_text().splitlines() if n.strip()]
    assert len(names) > 500
    failed = [n for n in names if parse(n).date is None]
    ratio = 1 - len(failed) / len(names)
    assert ratio >= 0.95, f"{ratio:.1%} parsed; failures: {failed}"
    # the three known no-date names (no show date in the folder name at all)
    assert set(failed) <= {"Soulshine.Benefit.Concert.flac16", "d&t.farm.aid.2024.siriusxm.flac16", "stage"}


def test_library_corpus_artist_codes():
    names = [n for n in (FIX / "library_names.txt").read_text().splitlines() if n.strip()]
    for n in names:
        code = parse(n).artist_code
        if n.startswith("dmb"):
            assert code == "dmb", n
        elif n.lower().startswith(("d+t", "d&t", "dt")):
            assert code == "d&t", n
