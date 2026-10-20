from datetime import date

from app import library
from app.library import LibraryIndex, date_from_text


def _show(root, rel, files=("01.flac",)):
    d = root / rel
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_bytes(b"x")
    return d


def test_date_from_text_variants():
    assert date_from_text("dmb2025-07-12.ca-11.flac16") == date(2025, 7, 12)
    assert date_from_text("d+t1996.02.16.siriusxm.web-dl.flac16") == date(1996, 2, 16)
    assert date_from_text("dmb20240424.flac24") == date(2024, 4, 24)
    assert date_from_text("dmb2021.9.22.flac.gf") == date(2021, 9, 22)
    assert date_from_text("(2021) DMB LIVE on Siriux XM - New York, NY, 11.12.21") == date(2021, 11, 12)
    assert date_from_text("The.Lillywhite.Sessions") is None


def test_scan_and_match_levels(tmp_path):
    _show(tmp_path, "DMB_Live/PrevYear/cy2023/dmb2023-06-23.siriusxm.flac16")
    _show(tmp_path, "DMB_Live/dmb2025-07-12.ca-11.flac16")
    _show(tmp_path, "DMB_Live/PrevYear/The.Lillywhite.Sessions")
    _show(tmp_path, "Dave_Tim/2022.09.24 - Raleigh, NC (Farmaid 2022)/ALAC (Apple Lossless)", ("a.m4a",))
    (tmp_path / "DMB_Live/PrevYear/cy2023/notes").mkdir()  # no audio: not a show
    idx = LibraryIndex(tmp_path, tmp_path / "idx.json")
    assert idx.rescan() == 4

    exact = idx.match("dmb2025-07-12.ca-11.flac16", "dmb", "2025-07-12", "taper")
    assert exact["level"] == "exact"
    same = idx.match("dmb2023-06-23.sxm.web-dl.flac16", "dmb", "2023-06-23", "siriusxm")
    assert same["level"] == "same"
    other = idx.match("dmb2023-06-23.dpa4011.flac16", "dmb", "2023-06-23", "taper")
    assert other["level"] == "other" and other["sources"] == ["siriusxm"]
    assert idx.match("dmb1999-01-01.flac16", "dmb", "1999-01-01", "taper") is None
    assert idx.match("d&t2022-09-24.flac16", "d&t", "2022-09-24", "taper")["level"] == "same"
    # artist must agree: a DMB show on a D&T date is not a match
    assert idx.match("dmb2022-09-24.flac16", "dmb", "2022-09-24", "taper") is None


def test_index_persists(tmp_path):
    _show(tmp_path, "DMB_Live/dmb2025-07-12.ca-11.flac16")
    LibraryIndex(tmp_path, tmp_path / "idx.json").rescan()
    again = LibraryIndex(tmp_path / "gone", tmp_path / "idx.json")
    assert len(again) == 1 and again.scanned_at


def test_unreadable_folder_is_skipped(tmp_path, monkeypatch):
    _show(tmp_path, "DMB_Live/dmb2025-07-12.ca-11.flac16")
    real = library.Path.iterdir

    def flaky(self):
        if self.name == "DMB_Live":
            raise OSError(5, "Input/output error")
        return real(self)

    monkeypatch.setattr(library.Path, "iterdir", flaky)
    assert library.scan(tmp_path) == []
