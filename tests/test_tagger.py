import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from mutagen.flac import FLAC

from app.models import ShowInfo, Track
from app.tagger import cover_mime, tag_show

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xc0\x00\x0b\x08\x00\x10\x00\x20\x01\x01\x11\x00" + b"\xff\xd9"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (32).to_bytes(4, "big") + (16).to_bytes(4, "big")
       + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00")
ALBUM = "2022-07-09: Saratoga Springs, NY"


def make_flacs(d: Path, n: int, names=None) -> list[Path]:
    out = []
    for i in range(n):
        p = d / (names[i] if names else f"dmb2022-07-09.t{i + 1:02d}.flac")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:a", "flac", str(p)], check=True)
        out.append(p)
    return out


def info_with(titles, discs=None, **kw) -> ShowInfo:
    tracks = [Track(number=i + 1, disc=(discs[i] if discs else None), title=t) for i, t in enumerate(titles)]
    return ShowInfo(date=date(2022, 7, 9), lineage=["Taper: Bob", "Source: ck63 > 722"], tracks=tracks, **kw)


def test_basic_tagging_and_cover(tmp_path):
    files = make_flacs(tmp_path, 3)
    info = info_with(["Intro", "Song Two", "Song Three"])
    info.tracks[0].segue = True
    warns = tag_show(files, info, ALBUM, "Dave Matthews Band", JPEG)
    assert warns == []
    f = FLAC(files[1])
    assert f["title"] == ["Song Two"] and f["album"] == [ALBUM]
    assert f["artist"] == ["Dave Matthews Band"] and f["albumartist"] == ["Dave Matthews Band"]
    assert f["tracknumber"] == ["2"] and f["tracktotal"] == ["3"]
    assert f["date"] == ["2022-07-09"] and f["genre"] == ["Live"]
    assert "discnumber" not in f
    assert f["comment"] == ["Taper: Bob\nSource: ck63 > 722"]
    assert "Segues" in FLAC(files[0])["comment"][0]
    pics = f.pictures
    assert len(pics) == 1 and pics[0].type == 3 and pics[0].mime == "image/jpeg"
    assert (pics[0].width, pics[0].height) == (32, 16)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(files[0]), "-f", "null", "-"], check=True)  # still decodes


def test_png_cover(tmp_path):
    files = make_flacs(tmp_path, 1)
    tag_show(files, info_with(["Only"]), ALBUM, "Dave Matthews Band", PNG)
    p = FLAC(files[0]).pictures[0]
    assert p.mime == "image/png" and (p.width, p.height) == (32, 16) and p.type == 3


def test_bad_cover_warns_and_skips(tmp_path):
    files = make_flacs(tmp_path, 1)
    warns = tag_show(files, info_with(["Only"]), ALBUM, "Dave Matthews Band", b"GIF89a....")
    assert any("cover" in w for w in warns) and FLAC(files[0]).pictures == []
    assert cover_mime(b"junk") is None


def test_no_cover_keeps_existing_picture_and_no_cover_ok(tmp_path):
    files = make_flacs(tmp_path, 1)
    tag_show(files, info_with(["A"]), ALBUM, "X", JPEG)
    tag_show(files, info_with(["B"]), ALBUM, "X", None)
    assert len(FLAC(files[0]).pictures) == 1 and FLAC(files[0])["title"] == ["B"]


def test_multi_disc(tmp_path):
    files = make_flacs(tmp_path, 4)
    info = info_with(["A", "B", "C", "D"], discs=[1, 1, 2, 2])
    tag_show(files, info, ALBUM, "Dave Matthews Band", None)
    f = FLAC(files[2])
    assert f["discnumber"] == ["2"] and f["disctotal"] == ["2"]
    assert f["tracknumber"] == ["1"] and f["tracktotal"] == ["2"] and f["title"] == ["C"]


def test_old_tags_cleared_replaygain_and_fingerprint_kept(tmp_path):
    files = make_flacs(tmp_path, 1)
    f = FLAC(files[0])
    f["title"] = "Old"
    f["album"] = "Old Album"
    f["comment"] = "old comment"
    f["replaygain_track_gain"] = "-3.2 dB"
    f["replaygain_album_peak"] = "0.99"
    f["acoustid_fingerprint"] = "AQAA"
    f["fingerprint"] = "abc"
    f["encoder"] = "xACT"
    f.save()
    tag_show(files, info_with(["New"]), ALBUM, "Dave Matthews Band", None)
    f = FLAC(files[0])
    assert f["title"] == ["New"] and f["album"] == [ALBUM] and f["comment"] != ["old comment"]
    assert f["replaygain_track_gain"] == ["-3.2 dB"] and f["replaygain_album_peak"] == ["0.99"]
    assert f["acoustid_fingerprint"] == ["AQAA"] and f["fingerprint"] == ["abc"] and f["encoder"] == ["xACT"]


def test_count_mismatch_keeps_titles_and_warns(tmp_path):
    files = make_flacs(tmp_path, 3, names=["dmb2022-07-09.t01.flac", "dmb2022-07-09.t02.flac", "03 Last Song.flac"])
    f = FLAC(files[1])
    f["title"] = "Existing Title"
    f.save()
    warns = tag_show(files, info_with(["One", "Two"]), ALBUM, "Dave Matthews Band", None)
    assert len(warns) == 1 and "2 tracks" in warns[0] and "3 files" in warns[0]
    assert FLAC(files[1])["title"] == ["Existing Title"]            # existing title kept
    assert FLAC(files[2])["title"] == ["Last Song"]                  # derived from the filename
    assert FLAC(files[0])["title"] == ["Track 01"]                   # only a number in the name
    assert FLAC(files[2])["tracknumber"] == ["3"] and FLAC(files[2])["tracktotal"] == ["3"]
    assert FLAC(files[2])["album"] == [ALBUM]


def test_no_setlist_warns(tmp_path):
    files = make_flacs(tmp_path, 2, names=["d1t01 Intro.flac", "d1t02 Crash.flac"])
    warns = tag_show(files, ShowInfo(date=date(2022, 7, 9)), ALBUM, "Dave Matthews Band", None)
    assert warns and "setlist" in warns[0]
    assert FLAC(files[0])["title"] == ["Intro"] and FLAC(files[1])["title"] == ["Crash"]
    assert FLAC(files[0])["date"] == ["2022-07-09"]


def test_date_from_album_prefix_over_info(tmp_path):
    files = make_flacs(tmp_path, 1)
    info = info_with(["A"])
    info.date = date(2022, 7, 8)
    tag_show(files, info, "2022-07-09: Saratoga Springs, NY", "X", None)
    assert FLAC(files[0])["date"] == ["2022-07-09"]


def test_inherited_titles_lose_track_numbers():
    from app.tagger import _strip_track_number
    assert _strip_track_number("01 Intro") == "Intro"
    assert _strip_track_number("1. Warehouse") == "Warehouse"
    assert _strip_track_number("d2t03 Two Step") == "Two Step"
    assert _strip_track_number("#41") == "#41"
    assert _strip_track_number("Ants Marching") == "Ants Marching"
