"""Tests for app/art.py. No network: dmbalmanac is served from tests/fixtures/art/."""
from __future__ import annotations

import logging
import struct
import zlib
from datetime import date
from pathlib import Path

import pytest
import requests

from app import art

FIXTURES = Path(__file__).parent / "fixtures" / "art" / "dmbalmanac"
# dmbalmanac.com pages are another site's content and are not distributed. Save a few listing
# (TourShow.aspx?where=<year>) and ShowMedia pages here to run the parsing and lookup tests.
_NO_PAGES = "dmbalmanac page fixtures are not distributed"
MIN = art.MIN_IMAGE_BYTES  # 10 KB


# ---------------------------------------------------------------- image helpers

def _png_bytes(size: int) -> bytes:
    """A valid PNG of at least `size` bytes (stored, uncompressed IDAT)."""
    width = 64
    rows = max(1, (size // (width * 3 + 1)) + 1)
    raw = b"".join(b"\x00" + bytes([(y * 7) % 256]) * (width * 3) for y in range(rows))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, rows, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 0)) + chunk(b"IEND", b""))


def _jpeg_bytes(size: int) -> bytes:
    """JPEG-signature bytes padded with a COM segment to `size` bytes."""
    body = b"\x00" * max(0, size - 6)
    return b"\xff\xd8\xff\xe0" + body[:65000] + b"\xff\xd9"


def _write_image(path: Path, size: int, marker: bytes = b"") -> Path:
    """Write a valid image of at least `size` bytes; `marker` makes content unique."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _png_bytes(size) if path.suffix.lower() == ".png" else _jpeg_bytes(size)
    path.write_bytes(data + marker)
    return path


class NoNetwork(BaseException):
    """Not an Exception, so art's own error handling cannot swallow it."""


@pytest.fixture
def no_network(monkeypatch):
    """Any dmbalmanac request fails the test."""
    def boom(url):
        raise NoNetwork(f"unexpected network access: {url}")
    monkeypatch.setattr(art, "_fetch", boom)


# -------------------------------------------------------- torrent-folder covers

def test_cover_name_beats_larger_image(tmp_path, no_network):
    big = _write_image(tmp_path / "scan_001.jpg", 40 * 1024)
    cover = _write_image(tmp_path / "cover.jpg", 20 * 1024)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == cover.read_bytes()
    assert big.read_bytes() != cover.read_bytes()


@pytest.mark.parametrize("name", ["front.jpg", "poster.png", "folder.jpeg", "artwork.webp.png", "frontcover.jpg"])
def test_preferred_names_are_chosen(tmp_path, no_network, name):
    _write_image(tmp_path / "huge_random.jpg", 60 * 1024)
    chosen = _write_image(tmp_path / name, 12 * 1024)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == chosen.read_bytes()


def test_largest_image_when_no_preferred_name(tmp_path, no_network):
    _write_image(tmp_path / "a.jpg", 20 * 1024, b"a")
    largest = _write_image(tmp_path / "b.png", 45 * 1024, b"b")
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == largest.read_bytes()


def test_images_under_10kb_are_skipped(tmp_path, no_network):
    _write_image(tmp_path / "cover.jpg", MIN - 1024)           # thumbnail-sized cover
    real = _write_image(tmp_path / "IMG_0001.jpg", MIN + 2048)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == real.read_bytes()


def test_back_tray_disc_only_used_when_alone(tmp_path, no_network):
    _write_image(tmp_path / "back.jpg", 50 * 1024)
    _write_image(tmp_path / "disc1_tray.jpg", 40 * 1024)
    scan = _write_image(tmp_path / "scan.jpg", 15 * 1024)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == scan.read_bytes()


def test_back_image_used_when_it_is_the_only_one(tmp_path, no_network):
    back = _write_image(tmp_path / "back.jpg", 15 * 1024)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == back.read_bytes()


def test_recursive_search_finds_nested_image(tmp_path, no_network):
    nested = _write_image(tmp_path / "Artwork" / "Scans" / "folder.png", 12 * 1024)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == nested.read_bytes()


def test_non_image_files_are_ignored(tmp_path, no_network):
    (tmp_path / "cover.txt").write_bytes(b"x" * (20 * 1024))
    (tmp_path / "cover.gif.nfo").write_bytes(b"x" * (20 * 1024))
    # "other" skips dmbalmanac, so only the folder is consulted.
    assert art.find_cover(tmp_path, date(2024, 4, 25), "other") is None


def test_file_with_image_extension_but_no_image_magic_is_skipped(tmp_path, no_network):
    (tmp_path / "cover.jpg").write_bytes(b"not an image" * 2000)
    real = _write_image(tmp_path / "other.png", 12 * 1024)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == real.read_bytes()


def test_missing_show_dir_returns_none_without_lookup_for_other_artist(tmp_path, no_network):
    assert art.find_cover(tmp_path / "nope", date(2024, 4, 25), "other") is None


def test_torrent_image_wins_over_poster(tmp_path, monkeypatch):
    local = _write_image(tmp_path / "cover.jpg", 12 * 1024)

    def boom(url):
        raise NoNetwork("dmbalmanac should not be queried")
    monkeypatch.setattr(art, "_fetch", boom)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb", tmp_path / "cache") == local.read_bytes()


# ------------------------------------------------- dmbalmanac parsing (fixtures)

def _listing(year: int) -> bytes:
    if not FIXTURES.exists():
        pytest.skip(_NO_PAGES)
    return (FIXTURES / f"tour-{year}.html").read_bytes()


def _media(show_date: str) -> bytes:
    if not FIXTURES.exists():
        pytest.skip(_NO_PAGES)
    return (FIXTURES / f"showmedia-{show_date}.html").read_bytes()


# date, artist code, fixture year, expected ShowMedia href (the poster.gif link)
POSTER_HITS = [
    ("2024-04-25", "dmb", 2024, "/ShowMedia.aspx?sid=453091087&tid=8179&mid=1886"),
    ("2023-06-23", "dmb", 2023, "/ShowMedia.aspx?sid=453090999&tid=8174&mid=1825"),
    ("2019-07-12", "dmb", 2019, "/ShowMedia.aspx?sid=453086596&tid=7160&mid=12490"),
    ("2015-07-04", "dmb", 2015, "/ShowMedia.aspx?sid=453060227&tid=128&mid=1370"),
    ("2010-08-20", "dmb", 2010, "/ShowMedia.aspx?sid=453056794&tid=110&mid=1096"),
    ("2008-06-14", "dmb", 2008, "/ShowMedia.aspx?sid=453056548&tid=98&mid=906"),
    ("2004-07-27", "dmb", 2004, "/ShowMedia.aspx?sid=453056101&tid=80&mid=634"),
    ("1996-07-20", "dmb", 1996, "/ShowMedia.aspx?sid=453055052&tid=67&mid=451"),
    ("2017-05-30", "d&t", 2017, "/ShowMedia.aspx?sid=453071427&tid=5154&mid=1532"),
]

# Shows that exist on the site but have no poster (ticket stub or nothing).
POSTER_MISSES = [
    ("2002-04-20", "dmb", 2002),   # ticketstub.gif only
    ("1994-10-27", "dmb", 1994),   # ticketstub.gif only
    ("1999-03-06", "dmb", 1999),   # Dave & Tim row, no poster; DMB lookup must miss
    ("2021-06-12", "dmb", 2021),   # no media icons
    ("2005-05-16", "dmb", 2005),   # Late Show appearance, cast.gif only
    ("2019-03-19", "dmb", 2019),   # setlist.gif only
    ("2001-11-30", "dmb", 2001),   # no media icons
    ("2024-09-26", "dmb", 2024),   # Delta Ballroom, no poster
]


@pytest.mark.parametrize("show_date,code,year,href", POSTER_HITS)
def test_listing_yields_poster_href(show_date, code, year, href):
    d = date.fromisoformat(show_date)
    assert art.parse_listing(_listing(year), d, code) == href


@pytest.mark.parametrize("show_date,code,year", POSTER_MISSES)
def test_listing_without_poster_returns_none(show_date, code, year):
    assert art.parse_listing(_listing(year), date.fromisoformat(show_date), code) is None


def test_artist_code_separates_dave_and_tim_from_dmb():
    listing = _listing(2017)
    d = date(2017, 5, 30)
    assert art.parse_listing(listing, d, "d&t") == "/ShowMedia.aspx?sid=453071427&tid=5154&mid=1532"
    assert art.parse_listing(listing, d, "dmb") is None
    assert art.parse_listing(listing, d, "dm") is None


def test_plus_t_alias_and_unknown_artist():
    listing = _listing(2017)
    d = date(2017, 5, 30)
    assert art.parse_listing(listing, d, "d+t") == art.parse_listing(listing, d, "d&t")
    assert art.parse_listing(listing, d, "other") is None


def test_wrong_year_page_does_not_match():
    # 2024-04-25 is not on the 2023 page.
    assert art.parse_listing(_listing(2023), date(2024, 4, 25), "dmb") is None


@pytest.mark.parametrize("show_date,code,year,href", POSTER_HITS)
def test_media_page_gives_full_size_image(show_date, code, year, href):
    mid = href.split("mid=")[1]
    src = art.parse_media_page(_media(show_date))
    assert src == f"/mediafiles/show/resized/{mid}.jpg"


def test_media_page_without_view_image_returns_none():
    assert art.parse_media_page(b"<html><body>no image</body></html>") is None


def test_listing_parser_ignores_non_show_rows():
    # The 1996-07-20 page also lists a "Blues Traveler (Dave guesting)" row whose
    # only media link is a ticket stub; the DMB lookup must return the HORDE poster.
    href = art.parse_listing(_listing(1996), date(1996, 7, 20), "dmb")
    assert href == "/ShowMedia.aspx?sid=453055052&tid=67&mid=451"


# ------------------------------------------- full lookup (fake fetch, no network)

class FakeWeb:
    """Serves fixture pages and a generated poster, counting each request."""

    def __init__(self, poster: bytes):
        self.poster = poster
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if "TourShow.aspx?where=" in url:
            return _listing(int(url.split("where=")[1]))
        if "ShowMedia.aspx" in url:
            mid = url.split("mid=")[1]
            for show_date, _code, _year, href in POSTER_HITS:
                if href.endswith(f"mid={mid}"):
                    return _media(show_date)
            raise AssertionError(f"no fixture for {url}")
        if "/mediafiles/show/resized/" in url:
            return self.poster
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb(_png_bytes(MIN + 4096))
    monkeypatch.setattr(art, "_fetch", fake)
    return fake


def test_poster_lookup_then_cache_hit(tmp_path, web):
    cache = tmp_path / "cache"
    first = art.find_cover(tmp_path / "empty", date(2024, 4, 25), "dmb", cache)
    assert first == web.poster
    assert len(web.calls) == 3  # listing, ShowMedia page, image
    assert web.calls[0] == "https://dmbalmanac.com/TourShow.aspx?where=2024"
    assert web.calls[1] == "https://dmbalmanac.com/ShowMedia.aspx?sid=453091087&tid=8179&mid=1886"
    assert web.calls[2] == "https://dmbalmanac.com/mediafiles/show/resized/1886.jpg"

    web.calls.clear()
    again = art.find_cover(tmp_path / "empty", date(2024, 4, 25), "dmb", cache)
    assert again == web.poster
    assert web.calls == []


def test_listing_is_fetched_once_per_year(tmp_path, web):
    cache = tmp_path / "cache"
    art.find_cover(tmp_path, date(2024, 4, 25), "dmb", cache)
    web.calls.clear()
    # Same year, different show without a poster: only the cached listing is used.
    assert art.find_cover(tmp_path, date(2024, 9, 26), "dmb", cache) is None
    assert web.calls == []


def test_negative_result_is_cached(tmp_path, web):
    cache = tmp_path / "cache"
    assert art.find_cover(tmp_path, date(2021, 6, 12), "dmb", cache) is None
    assert len(web.calls) == 1  # listing only
    assert (cache / "dmbalmanac" / "dmb-2021-06-12.none").exists()

    web.calls.clear()
    assert art.find_cover(tmp_path, date(2021, 6, 12), "dmb", cache) is None
    assert web.calls == []


def test_cache_keyed_by_date_and_artist(tmp_path, web):
    cache = tmp_path / "cache"
    assert art.find_cover(tmp_path, date(2017, 5, 30), "d&t", cache) == web.poster
    files = sorted(p.name for p in (cache / "dmbalmanac").iterdir())
    assert "dt-2017-05-30.poster" in files
    web.calls.clear()
    # The same date under the DMB code is a different show: no DMB row, so a separate negative marker.
    assert art.find_cover(tmp_path, date(2017, 5, 30), "dmb", cache) is None
    assert (cache / "dmbalmanac" / "dmb-2017-05-30.none").exists()
    assert web.calls == []  # the cached 2017 listing is reused


def test_lookup_works_without_cache_dir(tmp_path, web):
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == web.poster
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb") == web.poster
    assert len(web.calls) == 6


def test_network_error_returns_none_and_is_not_cached(tmp_path, monkeypatch, caplog):
    def down(url):
        raise requests.ConnectionError("no route to host")
    monkeypatch.setattr(art, "_fetch", down)
    cache = tmp_path / "cache"
    with caplog.at_level(logging.WARNING, logger="app.art"):
        assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb", cache) is None
    assert any("dmbalmanac" in r.getMessage() for r in caplog.records)
    assert not list((cache / "dmbalmanac").glob("*.none"))


def test_http_error_returns_none(tmp_path, monkeypatch):
    def not_found(url):
        raise requests.HTTPError("404")
    monkeypatch.setattr(art, "_fetch", not_found)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb", tmp_path / "c") is None


def test_non_image_poster_is_treated_as_no_poster(tmp_path, monkeypatch):
    fake = FakeWeb(b"<html>oops</html>" * 2000)
    monkeypatch.setattr(art, "_fetch", fake)
    assert art.find_cover(tmp_path, date(2024, 4, 25), "dmb", tmp_path / "c") is None


def test_fetch_sends_user_agent_and_timeout(monkeypatch):
    seen = {}

    class Resp:
        content = b"ok"

        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Resp()

    monkeypatch.setattr(art.requests, "get", fake_get)
    monkeypatch.setattr(art, "_last_request", 0.0)
    assert art._fetch("https://dmbalmanac.com/x") == b"ok"
    assert seen["headers"]["User-Agent"] == "dreamingtree-dl/0.1"
    assert seen["timeout"] == 15


def test_fetch_waits_between_requests(monkeypatch):
    slept = []

    class Resp:
        content = b"ok"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(art.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(art.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(art, "_last_request", art.time.monotonic())
    art._fetch("https://dmbalmanac.com/x")
    assert slept and 0 < slept[0] <= art.MIN_INTERVAL
