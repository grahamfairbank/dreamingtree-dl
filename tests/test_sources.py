import pytest

from app.models import ShowInfo, TorrentName
from app.sources import source_suffix


def tn(raw: str) -> TorrentName:
    import re
    return TorrentName(raw, "dmb", None, [t for t in re.split(r"[.\s_]+", raw.lower()) if t], None)


def info(source: str | None = None, *lineage: str) -> ShowInfo:
    return ShowInfo(source_line=source, lineage=list(lineage) or ([f"Source: {source}"] if source else []))


@pytest.mark.parametrize("raw,want", [
    ("dmb2025-07-11.siriusxm.flac16", "siriusxm"),
    ("dmb2018-05-30.siriusxm.web-dl.flac16", "siriusxm"),
    ("dmb2018-07-13.sirius-xm.flac16", "siriusxm"),
    ("dmb2023-07-14.sxm.flac16", "siriusxm"),
    ("dmb1994-10-27.sbd.reconvert.flac16", "sbd"),
    ("dmb1993-11-09.asbd.fixed-v2.flac", "sbd"),
    ("dmb1993-05-27 (set I).dsbd - New York City", "sbd"),
    ("dmb2001-01-01.fm.flac16", "fm"),
    ("dmb2024-07-20.webcast.flac16", "webcast"),
    ("dmb2024-07-20.web-dl.flac16", "webcast"),
    ("dmb2001-01-01.matrix.flac16", "matrix"),
    ("dmb2001-01-01.mtx.flac16", "matrix"),
    ("dmb2025-07-12.ca-11.flac16", ""),
    ("dmb2023-06-30.ck63.mp6ii.flac16", ""),
])
def test_name_tokens(raw, want):
    assert source_suffix(tn(raw), info()) == want


def test_tokens_are_checked_before_info():
    assert source_suffix(tn("dmb1994.sbd.flac16"), info("Neumann KM140 > DAT")) == "sbd"


def test_info_source_line_used_when_name_has_no_token():
    assert source_suffix(tn("dmb2019-07-12.flac16"), info('SiriusXM HLS "Extreme" quality stream > ffmpeg')) == "siriusxm"
    assert source_suffix(tn("dmb1993-07-14.new"), info("ASBD (unknown generation) -> CD")) == "sbd"
    assert source_suffix(tn("dmb2005-05-09"), info('Radio@AOL Webcast from "Stand Up" release party')) == "webcast"
    assert source_suffix(tn("dmb2003.x"), info("FM broadcast > cassette")) == "fm"


def test_mic_taper_is_blank():
    assert source_suffix(tn("dmb2021-08-25.akgck61.flac16"), info("AKG ck61 > Sound Devices MixPre-6ii")) == ""
    assert source_suffix(tn("dmb2024-11-23.ak50 - New York City"),
                         info("Inside Soundboard Cage/FOB/DFC>Neumann AK50's(DINa)>SD Mixpre6ii")) == ""
    assert source_suffix(tn("dmb2024-11-23"), ShowInfo()) == ""


def test_taper_plus_siriusxm_counts_as_taper():
    assert source_suffix(tn("dmb2023-06-02.ck61+siriusxm.flac16"), info("AKG ck61 > MixPre")) == ""
    assert source_suffix(tn("dmb2023-05-26.dpa4011+siriusxm.flac16"),
                         info("DPA 4011 > Sound Devices MixPre-6ii + SiriusXM stream rebroadcast capture")) == ""
    # a mic token alone with no sirius mention is also blank
    assert source_suffix(tn("dmb2023-06-02.siriusxm.flac16"), info("AKG ck61 > MixPre")) == ""


def test_result_is_always_lowercase():
    assert source_suffix(tn("DMB1994-10-27.SBD.FLAC16"), info()) == "sbd"
    assert source_suffix(tn("dmb2018.SiriusXM"), info()) == "siriusxm"


def test_web_and_tv_release_names():
    from app.names import parse_torrent_name
    from app.models import ShowInfo
    for raw, want in [("dm2026-10-03.youtube.flac16", "webcast"), ("d+t2026-09-26.webrip.flac16", "webcast"),
                      ("dmb2002-12-18.conan.flac16", "tv"), ("dm2004-01-09.tv.flac16", "tv"),
                      ("dmb2025-07-12.ca-11.flac16", ""), ("dmb2023-06-23.siriusxm.web-dl.flac16", "siriusxm")]:
        assert source_suffix(parse_torrent_name(raw), ShowInfo()) == want, raw
