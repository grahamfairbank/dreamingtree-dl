"""Tests for app/dreamingtree.py against captured (redacted) pages. No network."""
import json
from datetime import date
from pathlib import Path

import pytest
import requests

from app.config import DreamingTreeCfg
from app.dreamingtree import (DreamingTree, DreamingTreeError, LoginError, extract_download_params,
                              parse_detail_filelist, parse_listing, parse_size)

FIX = Path(__file__).parent / "fixtures" / "dt"
# Captured dreamingtree.org pages are not distributed (private tracker; they identify an account).
# Save your own logged-in pages here (with authkey/torrent_pass/IP removed) to run these tests.
if not FIX.exists():
    pytest.skip("dreamingtree.org page fixtures are not distributed", allow_module_level=True)


def fx(name: str) -> str:
    return (FIX / name).read_text()


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("text,expected", [
    ("244.29 MB (Max)", int(244.29 * 1024**2)), ("1.22 GB", int(1.22 * 1024**3)), ("17.34 KB", int(17.34 * 1024)),
    ("1,000.02 MB", int(1000.02 * 1024**2)), ("512 B", 512), ("", None), ("n/a", None),
])
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_grouped_listing_first_row():
    page = parse_listing(fx("browse_grouped_p1.html"))
    assert page.total == 5180 and page.pages == 104 and len(page.rows) == 50
    r = page.rows[0]
    assert r.torrent_id == "5717"
    assert r.name == "d+t2026-09-26.webrip.flac16"
    assert r.artist == "Dave Matthews & Tim Reynolds"
    assert r.show_date == date(2026, 9, 26)
    assert r.size_bytes == int(244.29 * 1024**2)
    assert (r.snatches, r.seeders, r.leechers) == (61, 29, 0)
    assert r.added == "Oct 16 2026, 09:35"
    assert r.download_url == ("https://dreamingtree.org/torrents.php?action=download&id=5717"
                              "&authkey=REDACTED&torrent_pass=REDACTED")
    assert r.detail_url == "https://dreamingtree.org/torrents.php?id=5588&torrentid=5717"
    assert page.info["5717"] == "FLAC / Lossless"


def test_grouped_listing_group_title_is_the_name_and_ids_are_unique():
    page = parse_listing(fx("browse_grouped_p1.html"))
    second = page.rows[1]
    assert second.name == "dmb1998-07-25.cmc64 - Camden (2026 Remix)"
    assert second.show_date == date(1998, 7, 25)
    assert second.torrent_id == "5716"
    assert len({r.torrent_id for r in page.rows}) == len(page.rows)


def test_grouped_group_with_two_torrents_gives_two_rows():
    page = parse_listing(fx("browse_grouped_siriusxm.html"))
    assert len(page.rows) == 52  # 50 groups, two of them hold a second torrent
    same = [r for r in page.rows if r.name == "dmb2023-09-01.siriusxm.flac24"]
    assert len(same) == 2 and same[0].torrent_id != same[1].torrent_id
    assert {r.size_bytes for r in same} == {int(1.56 * 1024**3), int(792.21 * 1024**2)}


def test_ungrouped_listing():
    page = parse_listing(fx("browse_ungrouped_shn.html"), page=1)
    assert page.total == 1177 and page.pages == 24 and len(page.rows) == 50
    r = page.rows[0]
    assert (r.torrent_id, r.name, r.artist) == ("3620", "dm2001-10-02.tv.shnf", "Dave Matthews")
    assert r.show_date == date(2001, 10, 2)
    assert r.size_bytes == int(17.34 * 1024**2)
    assert (r.snatches, r.seeders, r.leechers) == (40, 7, 0)
    assert r.added == "Apr 25 2018, 04:39"
    assert r.detail_url == "https://dreamingtree.org/torrents.php?id=3566&torrentid=3620"
    assert page.info["3620"] == "SHN / Lossless / Soundboard"


def test_ungrouped_artist_with_slash_and_odd_names():
    page = parse_listing(fx("browse_ungrouped_shn.html"))
    by_id = {r.torrent_id: r for r in page.rows}
    assert by_id["1989"].artist == "Phish/Dave Matthews Band"
    assert by_id["2435"].show_date is None            # 'dm1989-xx-xx_Mothers Night Tape'
    assert by_id["1480"].name == "d&t2007-02-23.mp3"


@pytest.mark.parametrize("name", ["browse_grouped_p1.html", "browse_grouped_p104.html", "browse_grouped_siriusxm.html",
                                  "browse_ungrouped_shn.html", "browse_ungrouped_siriusxm.html",
                                  "browse_ungrouped_europe.html"])
def test_every_fixture_row_is_complete(name):
    page = parse_listing(fx(name))
    assert page.rows
    for r in page.rows:
        assert r.torrent_id.isdigit() and r.name and r.artist
        assert r.size_bytes and r.seeders is not None and r.leechers is not None and r.snatches is not None
        assert r.added and "action=download&id=" + r.torrent_id in r.download_url
    dated = sum(1 for r in page.rows if r.show_date)
    assert dated >= len(page.rows) - 2


def test_pages_without_a_result_count_fall_back_to_the_pager():
    html = fx("browse_grouped_p1.html").replace("5,180 Results", "")
    assert parse_listing(html).pages == 104      # from the 'Last >>' link


def test_empty_result_page():
    page = parse_listing("<html><body><div class='box pad'>Your search did not match anything.</div></body></html>")
    assert page.rows == [] and page.total is None


def test_download_params_extracted_from_listing():
    assert extract_download_params(fx("browse_grouped_p1.html")) == {"authkey": "REDACTED",
                                                                      "torrent_pass": "REDACTED"}
    assert extract_download_params("<html></html>") == {}


def test_detail_page_filelist():
    folder, files = parse_detail_filelist(fx("detail_5587.html"))
    assert folder == "dmb1998-07-25.cmc64 - Camden (2026 Remix)"
    names = [f for f, _ in files]
    assert "dmb1998-07-25.cmc64.txt" in names and "flac (level 8)/01 - LeRoi solo (1998-07-25).flac" in names
    assert dict(files)["dmb1998-07-25.cmc64.txt"] == int(1.82 * 1024)
    assert "flac (level 8)/10 - Don't Drink the Water (1998-07-25).flac" in names   # HTML entities decoded


# ------------------------------------------------------------------ client (fake transport)
def resp(status=200, body: str | bytes = "", headers=None, url="https://dreamingtree.org/x") -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body.encode() if isinstance(body, str) else body
    r.headers.update({"Content-Type": "text/html; charset=UTF-8", **(headers or {})})
    r.url = url
    return r


class FakeSession(requests.Session):
    """Replays scripted responses and records (method, url, kwargs)."""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        item = self.script.pop(0)
        r = item(self) if callable(item) else item
        return r


TORRENT = b"d8:announce3:abc4:infod6:lengthi1e4:name1:x12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee"
LOGIN_FORM = fx("login.html")
CFG = DreamingTreeCfg(base_url="https://dreamingtree.org", username="u", password="p")
REDIRECT_LOGIN = lambda: resp(302, "", {"Location": "login.php"})  # noqa: E731


def set_cookie(s):
    s.cookies.set("session", "abc123", domain="dreamingtree.org", path="/")
    return resp(302, "", {"Location": "index.php"})


def make(script, tmp_path=None, **kw):
    s = FakeSession(script)
    cookie = str(tmp_path / "cookies.json") if tmp_path else None
    return DreamingTree(CFG, cookie, min_interval=0, session=s, **kw), s


def test_login_posts_form_and_saves_cookies(tmp_path):
    dt, s = make([set_cookie], tmp_path)
    dt.login()
    method, url, kw = s.calls[0]
    assert (method, url) == ("POST", "https://dreamingtree.org/login.php")
    assert kw["data"] == {"username": "u", "password": "p", "keeplogged": "1", "login": "Log in"}
    assert s.headers["User-Agent"] == "dreamingtree-dl/0.1"
    saved = json.loads((tmp_path / "cookies.json").read_text())
    assert saved[0]["name"] == "session" and saved[0]["value"] == "abc123"
    assert (tmp_path / "cookies.json").stat().st_mode & 0o777 == 0o600


def test_failed_login_raises_without_leaking_password():
    dt, _ = make([resp(200, fx("login_bad.html"))])
    with pytest.raises(LoginError) as e:
        dt.login()
    assert "invalid password" in str(e.value)
    assert CFG.password not in str(e.value).split("failed:")[0]


def test_login_without_credentials_does_not_hit_the_network():
    s = FakeSession([])
    dt = DreamingTree(DreamingTreeCfg(), None, min_interval=0, session=s)
    with pytest.raises(LoginError):
        dt.login()
    assert s.calls == []


def test_cookies_are_restored_from_file(tmp_path):
    (tmp_path / "cookies.json").write_text(json.dumps(
        [{"name": "session", "value": "old", "domain": "dreamingtree.org", "path": "/"}]))
    dt, s = make([resp(200, fx("browse_grouped_p1.html"))], tmp_path)
    rows = dt.list_torrents(1)
    assert len(rows) == 50
    assert s.cookies.get("session") == "old"
    assert [c[0] for c in s.calls] == ["GET"]       # no login needed


def test_list_torrents_builds_the_search_query():
    dt, s = make([resp(200, fx("browse_ungrouped_shn.html"))])
    dt.list_torrents(page=3, query="siriusxm")
    _, url, kw = s.calls[0]
    assert url == "https://dreamingtree.org/torrents.php"
    assert kw["params"] == {"action": "advanced", "searchsubmit": "1", "order_by": "time", "order_way": "desc",
                            "page": "3", "searchstr": "siriusxm"}


def test_list_page_artist_filter_and_totals():
    dt, s = make([resp(200, fx("browse_ungrouped_shn.html"))])
    page = dt.list_page(1, artist="Dave Matthews")
    assert s.calls[0][2]["params"]["artistname"] == "Dave Matthews"
    assert page.total == 1177 and page.pages == 24


def test_expired_session_triggers_one_relogin_and_retry(tmp_path):
    dt, s = make([REDIRECT_LOGIN(), set_cookie, resp(200, fx("browse_grouped_p1.html"))], tmp_path)
    rows = dt.list_torrents(1)
    assert len(rows) == 50
    assert [(m, u.rsplit("/", 1)[1]) for m, u, _ in s.calls] == [
        ("GET", "torrents.php"), ("POST", "login.php"), ("GET", "torrents.php")]
    assert json.loads((tmp_path / "cookies.json").read_text())[0]["value"] == "abc123"


def test_login_form_served_with_200_also_triggers_relogin():
    dt, s = make([resp(200, LOGIN_FORM), set_cookie, resp(200, fx("browse_grouped_p1.html"))])
    assert len(dt.list_torrents(1)) == 50
    assert s.calls[1][0] == "POST"


def test_still_redirected_after_login_raises():
    dt, _ = make([REDIRECT_LOGIN(), set_cookie, REDIRECT_LOGIN()])
    with pytest.raises(LoginError):
        dt.list_torrents(1)


def test_download_uses_credentials_from_a_listing_page():
    dt, s = make([resp(200, fx("browse_grouped_p1.html")),
                  resp(200, TORRENT, {"Content-Type": "application/x-bittorrent"})])
    assert dt.download_torrent("3638") == TORRENT
    _, url, kw = s.calls[1]
    assert url == "https://dreamingtree.org/torrents.php"
    assert kw["params"] == {"action": "download", "id": "3638", "authkey": "REDACTED", "torrent_pass": "REDACTED"}


def test_second_download_reuses_cached_credentials():
    dt, s = make([resp(200, fx("browse_grouped_p1.html")), resp(200, TORRENT), resp(200, TORRENT)])
    dt.download_torrent("1")
    dt.download_torrent("2")
    assert len(s.calls) == 3


def test_download_that_returns_html_refreshes_and_retries_once():
    page = fx("browse_grouped_p1.html")
    dt, s = make([resp(200, page), resp(200, "<html>Invalid authkey</html>"), set_cookie, resp(200, page),
                  resp(200, TORRENT)])
    assert dt.download_torrent("7") == TORRENT
    assert [m for m, _, _ in s.calls] == ["GET", "GET", "POST", "GET", "GET"]


def test_download_that_keeps_returning_html_raises():
    page = fx("browse_grouped_p1.html")
    dt, _ = make([resp(200, page), resp(200, "<html>nope</html>"), set_cookie, resp(200, page),
                  resp(200, "<html>nope</html>")])
    with pytest.raises(DreamingTreeError):
        dt.download_torrent("7")


def test_requests_are_throttled(monkeypatch):
    import app.dreamingtree as mod
    sleeps = []
    clock = [100.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mod.time, "sleep", lambda t: (sleeps.append(round(t, 2)), clock.__setitem__(0, clock[0] + t)))
    s = FakeSession([resp(200, fx("browse_grouped_p1.html")), resp(200, fx("browse_grouped_p1.html"))])
    dt = DreamingTree(CFG, None, min_interval=1.0, session=s)
    dt.list_torrents(1)
    clock[0] += 0.25
    dt.list_torrents(2)
    assert sleeps == [0.75]


def test_corrupt_cookie_file_is_ignored(tmp_path):
    (tmp_path / "cookies.json").write_text("{not json")
    dt, _ = make([], tmp_path)
    assert dt.s.cookies.get("session") is None
