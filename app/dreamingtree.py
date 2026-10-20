"""dreamingtree.org client (Gazelle).

Login: POST {base_url}/login.php with username, password, keeplogged=1, login='Log in'.
Use https://dreamingtree.org (the www host hangs during TLS). Be polite: at most 1 request/second.

Site facts (see docs and tests/fixtures/dt/):
* An expired or missing session makes any page answer ``302 -> login.php``.
* A failed login answers 200 with ``<span class="warning">You entered an invalid password``.
* ``torrents.php?action=advanced&searchsubmit=1`` (no ``group_results``) returns the *ungrouped*
  layout: one ``tr.torrent`` per torrent. Plain ``torrents.php`` returns the *grouped* layout
  (``tr.group`` followed by ``tr.group_torrent`` rows). Both are parsed here; the client asks for
  the ungrouped one because it has one self-contained row per downloadable torrent.
* The download link needs ``authkey`` and ``torrent_pass``; both are in every logged-in page.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from .config import DreamingTreeCfg
from .models import ListingRow
from .names import parse_torrent_name

USER_AGENT = "dreamingtree-dl/0.1"
MIN_INTERVAL = 1.0  # seconds between requests
PAGE_SIZE = 50


class DreamingTreeError(RuntimeError):
    pass


class LoginError(DreamingTreeError):
    pass


@dataclass
class ListingPage:
    rows: list[ListingRow]
    total: int | None = None      # "5,180 Results"
    pages: int | None = None      # number of pages of PAGE_SIZE
    page: int = 1
    # extra per-torrent facts keyed by torrent_id (not in ListingRow): e.g. 'FLAC / Lossless / Microphone'
    info: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- parsing helpers
_SIZE_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*([KMGT]?B)\b", re.I)
_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def parse_size(text: str) -> int | None:
    """'1,000.02 MB' -> bytes (binary units, as Gazelle uses). Trailing '(Max)' etc. is ignored."""
    m = _SIZE_RE.search(text or "")
    if not m:
        return None
    return int(float(m.group(1).replace(",", "")) * _UNITS[m.group(2).upper()])


def _int(text: str) -> int | None:
    t = (text or "").strip().replace(",", "")
    return int(t) if t.isdigit() else None


def _text(el: Tag | None) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def _query(href: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(href.replace("&amp;", "&")).query).items()}


def _row_from_cells(tr: Tag, *, artist: str | None, name: str, base_url: str,
                    dl: Tag, detail_href: str | None) -> ListingRow:
    """Build a ListingRow from a torrent row; the last 5 cells are Files? Time Size Snatch Seed Leech."""
    tds = tr.find_all("td", recursive=False)
    nums = [_text(td) for td in tds[-3:]]
    size_td = tds[-4]
    time_span = tr.select_one("span.time")
    added = None
    if time_span is not None:
        added = time_span.get("title") or _text(time_span)
    # torrent rows inside a group have no time cell of their own in some layouts
    return ListingRow(
        torrent_id=_query(dl["href"]).get("id", ""),
        name=name,
        artist=artist,
        show_date=parse_torrent_name(name).date,
        size_bytes=parse_size(_text(size_td)),
        seeders=_int(nums[1]),
        leechers=_int(nums[2]),
        snatches=_int(nums[0]),
        added=added,
        download_url=urljoin(base_url + "/", dl["href"].replace("&amp;", "&")),
        detail_url=urljoin(base_url + "/", detail_href.replace("&amp;", "&")) if detail_href else None,
    )


def _parse_ungrouped(table: Tag, base_url: str, info: dict[str, str]) -> list[ListingRow]:
    rows: list[ListingRow] = []
    for tr in table.find_all("tr", class_="torrent"):
        dl = tr.select_one('a[href*="action=download"]')
        gi = tr.select_one("div.group_info")
        if dl is None or gi is None:
            continue
        links = [a for a in gi.find_all("a", recursive=False)]
        title_a = next((a for a in links if "torrents.php" in a.get("href", "")), None)
        artist_links = [a for a in links if a.get("href", "").startswith("artist.php")]
        if title_a is None:
            continue
        row = _row_from_cells(tr, artist=" & ".join(_text(a) for a in artist_links) or None,
                              name=_text(title_a), base_url=base_url, dl=dl,
                              detail_href=title_a["href"].split("#")[0])
        rows.append(row)
        info[row.torrent_id] = _text(tr.select_one("div.torrent_info"))
    return rows


def _parse_grouped(table: Tag, base_url: str, info: dict[str, str]) -> list[ListingRow]:
    rows: list[ListingRow] = []
    artist: str | None = None
    name = ""
    group_href: str | None = None
    for tr in table.find_all("tr"):
        cls = tr.get("class") or []
        if "group" in cls:
            gi = tr.select_one("div.group_info")
            if gi is None:
                continue
            links = gi.find_all("a", recursive=False)
            artist_links = [a for a in links if a.get("href", "").startswith("artist.php")]
            title_a = next((a for a in links if a.get("href", "").startswith("torrents.php?id=")), None)
            artist = " & ".join(_text(a) for a in artist_links) or None
            name = _text(title_a)
            group_href = title_a["href"] if title_a else None
        elif "group_torrent" in cls and tr.find("td", class_="edition_info") is None:
            dl = tr.select_one('a[href*="action=download"]')
            if dl is None:
                continue
            tid = _query(dl["href"]).get("id", "")
            detail = f"{group_href}&torrentid={tid}" if group_href else None
            row = _row_from_cells(tr, artist=artist, name=name, base_url=base_url, dl=dl, detail_href=detail)
            rows.append(row)
            # 'FLAC / Lossless' (format/encoding) is the link text after the raquo; media is in the edition row
            fmt_a = tr.select("td a")[-1] if tr.select("td a") else None
            info[tid] = _text(fmt_a)
    return rows


def parse_listing(html: str, base_url: str = "https://dreamingtree.org", page: int = 1) -> ListingPage:
    """Parse a torrents.php result page (grouped or ungrouped layout)."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="torrent_table")
    info: dict[str, str] = {}
    rows: list[ListingRow] = []
    if table is not None:
        if "no_grouping" in (table.get("class") or []) or table.find("tr", class_="torrent"):
            rows = _parse_ungrouped(table, base_url, info)
        else:
            rows = _parse_grouped(table, base_url, info)
    total = None
    span = soup.select_one("div.ft_submit span")
    if span is not None:
        m = re.search(r"([\d,]+)\s+Results", span.get_text())
        total = int(m.group(1).replace(",", "")) if m else None
    pages = _page_count(soup, page)
    if total is not None:
        pages = max(1, -(-total // PAGE_SIZE))
    return ListingPage(rows=rows, total=total, pages=pages, page=page, info=info)


def _page_count(soup: BeautifulSoup, page: int) -> int | None:
    best = page
    for a in soup.select("div.linkbox a"):
        q = _query(a.get("href", ""))
        if q.get("page", "").isdigit():
            best = max(best, int(q["page"]))
    return best


def extract_download_params(html: str) -> dict[str, str]:
    """Pull authkey + torrent_pass out of any logged-in page's first download link."""
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select('a[href*="action=download"]'):
        q = _query(a["href"])
        if "authkey" in q and "torrent_pass" in q:
            return {"authkey": q["authkey"], "torrent_pass": q["torrent_pass"]}
    return {}


def parse_detail_filelist(html: str) -> tuple[str | None, list[tuple[str, int | None]]]:
    """Detail page -> (torrent folder name, [(path, size_bytes)]). Name comes from the file-list header."""
    soup = BeautifulSoup(html, "lxml")
    path = soup.select_one(".filelist_path")
    folder = _text(path).strip("/") or None
    files = []
    for tr in soup.select("table.filelist_table tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) == 2:
            files.append((tds[0].get_text(), parse_size(tds[1].get_text())))
    return folder, files


# --------------------------------------------------------------------------- client
class DreamingTree:
    def __init__(self, cfg: DreamingTreeCfg, cookie_path: str | None = None, *,
                 min_interval: float = MIN_INTERVAL, session: requests.Session | None = None,
                 timeout: float = 30.0):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/")
        self.cookie_path = Path(cookie_path) if cookie_path else None
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self._dl_params: dict[str, str] = {}
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._load_cookies()

    # -- cookies ----------------------------------------------------------
    def _load_cookies(self) -> None:
        if not self.cookie_path or not self.cookie_path.exists():
            return
        try:
            for c in json.loads(self.cookie_path.read_text()):
                self.s.cookies.set(c["name"], c["value"], domain=c.get("domain") or "", path=c.get("path", "/"))
        except (OSError, ValueError, KeyError):
            pass  # a corrupt cookie file just means we log in again

    def _save_cookies(self) -> None:
        if not self.cookie_path:
            return
        data = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self.s.cookies]
        self.cookie_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cookie_path.with_suffix(self.cookie_path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.cookie_path)

    # -- http -------------------------------------------------------------
    def _throttle(self) -> None:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _send(self, method: str, path: str, **kw) -> requests.Response:
        self._throttle()
        return self.s.request(method, self.base + "/" + path.lstrip("/"), timeout=self.timeout,
                              allow_redirects=False, **kw)

    @staticmethod
    def _is_login_redirect(r: requests.Response) -> bool:
        return r.is_redirect and "login.php" in (r.headers.get("Location") or "")

    def _get(self, path: str, **kw) -> requests.Response:
        """GET with automatic (single) re-login when the session has expired."""
        r = self._send("GET", path, **kw)
        if self._is_login_redirect(r) or self._looks_like_login_form(r):
            self.login()
            r = self._send("GET", path, **kw)
            if self._is_login_redirect(r):
                raise LoginError("still redirected to login after logging in")
        if r.is_redirect:  # e.g. torrents.php?id=.. canonicalisation; follow once
            r = self._send("GET", r.headers["Location"])
        r.raise_for_status()
        return r

    @staticmethod
    def _looks_like_login_form(r: requests.Response) -> bool:
        ct = r.headers.get("Content-Type", "")
        return r.status_code == 200 and "html" in ct and 'id="loginform"' in r.text[:20000]

    def login(self) -> None:
        if not self.cfg.username or not self.cfg.password:
            raise LoginError("dreamingtree username/password not configured")
        self.s.cookies.clear()
        r = self._send("POST", "login.php", data={"username": self.cfg.username, "password": self.cfg.password,
                                                   "keeplogged": "1", "login": "Log in"})
        if not r.is_redirect or "login.php" in (r.headers.get("Location") or ""):
            warns = [_text(w) for w in BeautifulSoup(r.text, "lxml").select(".warning")]
            warns = [w for w in warns if w and "JavaScript" not in w and "cookies" not in w]
            msg = warns[0] if warns else f"HTTP {r.status_code}"
            raise LoginError(f"dreamingtree login failed: {msg}")
        self._dl_params = {}
        self._save_cookies()

    # -- public API -------------------------------------------------------
    def list_page(self, page: int = 1, query: str = "", artist: str = "", year: int | None = None) -> ListingPage:
        """One browse page, newest first. The site's search stops at 1,000 matches (pages past 20
        repeat page 20), so a full crawl must slice by `year`."""
        params = {"action": "advanced", "searchsubmit": "1", "order_by": "time", "order_way": "desc",
                  "page": str(max(1, page))}
        if query:
            params["searchstr"] = query
        if artist:
            params["artistname"] = artist
        if year is not None:
            params["year"] = str(year)
        r = self._get("torrents.php", params=params)
        self._save_cookies()
        dl = extract_download_params(r.text)
        if dl:
            self._dl_params = dl
        return parse_listing(r.text, self.base, page)

    def list_torrents(self, page: int = 1, query: str = "") -> list[ListingRow]:
        return self.list_page(page, query).rows

    def detail(self, torrent_id: str, group_id: str | None = None) -> tuple[str | None, list[tuple[str, int | None]]]:
        """(folder name, files) of a torrent from its detail page. Needs the group id when known."""
        params = {"torrentid": torrent_id} if group_id is None else {"id": group_id, "torrentid": torrent_id}
        r = self._get("torrents.php", params=params)
        return parse_detail_filelist(r.text)

    def download_torrent(self, torrent_id: str) -> bytes:
        for attempt in range(2):
            if not self._dl_params:
                self.list_page(1)  # any page carries authkey + torrent_pass
            params = {"action": "download", "id": str(torrent_id), **self._dl_params}
            r = self._get("torrents.php", params=params)
            if r.content[:1] == b"d" and b"4:info" in r.content[:2000]:
                return r.content
            # got HTML (stale authkey, banned, no such torrent): refresh credentials once
            if attempt == 0:
                self._dl_params = {}
                self.login()
        raise DreamingTreeError(f"torrent {torrent_id}: response was not a .torrent file")
