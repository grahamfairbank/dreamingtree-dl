"""Find cover art: image inside the torrent, then the dmbalmanac.com poster, then None.

dmbalmanac lookup:
  1. GET /TourShow.aspx?where=<year>        one listing per year, table.show-list
     row: td[0] date "MM.DD.YY"; td[1] holds <a href="/ShowMedia.aspx?sid&tid&mid">
     with <img src="/images/poster.gif">; td[2] has span.detailgreen with the act
     label ("" or "(annotation)" = DMB, "Dave & Tim..." = d&t, "Dave solo..." = dm).
  2. GET /ShowMedia.aspx?sid&tid&mid       the poster page; img#view_image src is the
     full-size image, /mediafiles/show/resized/<mid>.jpg (thumbs/ is the thumbnail).
  3. GET that image.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MIN_IMAGE_BYTES = 10 * 1024
EXCLUDED_WORDS = ("back", "tray", "disc")          # substrings of the file stem
PREFERRED_WORDS = ("cover", "front", "poster", "folder")  # substrings of a name token
PREFERRED_TOKENS = {"art", "artwork"}              # exact name tokens only

DMB_BASE = "https://dmbalmanac.com"
DMB_LISTING_URL = DMB_BASE + "/TourShow.aspx?where={year}"
USER_AGENT = "dreamingtree-dl/0.1"
TIMEOUT = 15
MIN_INTERVAL = 1.0                 # seconds between dmbalmanac requests
LISTING_TTL = timedelta(days=1)    # current/future years; past years never expire
NEGATIVE_TTL = timedelta(days=30)  # "no poster" markers

_ARTIST_SLUG = {"dmb": "dmb", "d&t": "dt", "dm": "dm"}
_throttle_lock = threading.Lock()
_last_request = 0.0


# ---------------------------------------------------------------- entry point

def find_cover(show_dir: Path, show_date: date, artist_code: str,
               cache_dir: Path | None = None, *, use_torrent_image: bool = True,
               use_dmbalmanac: bool = True) -> bytes | None:
    """Return cover image bytes for a show, or None. Never raises."""
    local = None
    if use_torrent_image:
        try:
            local = _local_cover(Path(show_dir))
        except Exception as exc:  # disk problems must not break a job
            log.warning("reading images in %s failed: %s", show_dir, exc)
    if local is not None:
        return local
    if not use_dmbalmanac:
        return None
    return _poster_from_dmbalmanac(show_date, artist_code, cache_dir)


# ---------------------------------------------------------- torrent folder art

def _local_cover(show_dir: Path) -> bytes | None:
    if not show_dir.is_dir():
        return None
    images = []
    for p in show_dir.rglob("*"):
        if p.suffix.lower() not in IMAGE_SUFFIXES or not p.is_file():
            continue
        size = p.stat().st_size
        if size >= MIN_IMAGE_BYTES:
            images.append((p, size))
    if not images:
        return None
    # back/tray/disc images only count when nothing else is available.
    normal = [c for c in images if not _is_excluded(c[0])]
    pool = normal or images
    # Preferred names first, then largest file.
    pool.sort(key=lambda c: (_is_preferred(c[0]), c[1]), reverse=True)
    for path, _size in pool:
        data = path.read_bytes()
        if _is_image(data):
            log.debug("cover from torrent image %s", path)
            return data
        log.debug("skipping %s: not an image", path)
    return None


def _is_excluded(path: Path) -> bool:
    stem = path.stem.lower()
    return any(word in stem for word in EXCLUDED_WORDS)


def _is_preferred(path: Path) -> bool:
    for token in re.split(r"[^a-z0-9]+", path.stem.lower()):
        if token in PREFERRED_TOKENS or any(word in token for word in PREFERRED_WORDS):
            return True
    return False


def _is_image(data: bytes) -> bool:
    return (data.startswith(b"\xff\xd8\xff")
            or data.startswith(b"\x89PNG\r\n\x1a\n")
            or data.startswith(b"GIF87a") or data.startswith(b"GIF89a")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


# ------------------------------------------------------------ dmbalmanac poster

def _normalise_artist(code: str) -> str | None:
    code = (code or "").strip().lower()
    if code == "d+t":
        code = "d&t"
    return code if code in _ARTIST_SLUG else None


def _row_artist(label: str) -> str:
    """Map a listing row's act label to an artist code."""
    if label.startswith("Dave & Tim"):
        return "d&t"
    if label.startswith("Dave solo"):
        return "dm"
    if label == "" or label.startswith("("):
        return "dmb"
    return "other"


def _parse_listing_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%m.%d.%y").date()
    except ValueError:
        return None


def parse_listing(html: bytes | str, show_date: date, artist_code: str) -> str | None:
    """Return the ShowMedia.aspx href of the poster for this date and artist, or None."""
    want = _normalise_artist(artist_code)
    if want is None:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.select("table.show-list tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3 or _parse_listing_date(tds[0].get_text(" ", strip=True)) != show_date:
            continue
        label_tag = tds[2].select_one(".detailgreen")
        label = label_tag.get_text(" ", strip=True) if label_tag else ""
        if _row_artist(label) != want:
            continue
        href = _poster_href(tds[1])
        if href:
            return href
    return None


def _poster_href(cell) -> str | None:
    for a in cell.find_all("a", href=True):
        if "ShowMedia.aspx" in a["href"] and a.find("img", src=re.compile(r"poster\.gif")):
            return a["href"]
    return None


def parse_media_page(html: bytes | str) -> str | None:
    """Return the src of the full-size poster on a ShowMedia.aspx page."""
    img = BeautifulSoup(html, "html.parser").select_one("img#view_image")
    return img.get("src") if img else None


def _poster_from_dmbalmanac(show_date: date, artist_code: str,
                            cache_dir: Path | None) -> bytes | None:
    code = _normalise_artist(artist_code)
    if code is None:
        return None  # not a band dmbalmanac covers
    cache = _cache_subdir(cache_dir)
    try:
        return _lookup(show_date, code, cache)
    except Exception as exc:  # network, parse or disk: log and carry on
        log.warning("dmbalmanac lookup failed for %s %s: %s", code, show_date, exc)
        return None


def _lookup(show_date: date, code: str, cache: Path | None) -> bytes | None:
    key = f"{_ARTIST_SLUG[code]}-{show_date.isoformat()}"
    if cache is not None:
        poster_file = cache / f"{key}.poster"
        if poster_file.is_file():
            return poster_file.read_bytes()
        if _fresh(cache / f"{key}.none", NEGATIVE_TTL):
            return None

    listing = _listing_html(show_date.year, cache)
    href = parse_listing(listing, show_date, code)
    if href is None:
        _mark_none(cache, key)
        return None

    media = _media_html(href, key, cache)
    src = parse_media_page(media)
    if not src:
        _mark_none(cache, key)
        return None

    image = _fetch(urljoin(DMB_BASE, src))
    if not _is_image(image):
        log.warning("dmbalmanac poster for %s is not an image", key)
        _mark_none(cache, key)
        return None
    if cache is not None:
        _write(cache / f"{key}.poster", image)
    return image


def _listing_html(year: int, cache: Path | None) -> bytes:
    path = cache / f"listing-{year}.html" if cache is not None else None
    ttl = None if year < date.today().year else LISTING_TTL
    if path is not None and _fresh(path, ttl):
        return path.read_bytes()
    html = _fetch(DMB_LISTING_URL.format(year=year))
    if path is not None:
        _write(path, html)
    return html


def _media_html(href: str, key: str, cache: Path | None) -> bytes:
    path = cache / f"{key}.media.html" if cache is not None else None
    if path is not None and path.is_file():
        return path.read_bytes()
    html = _fetch(urljoin(DMB_BASE + "/", href))
    if path is not None:
        _write(path, html)
    return html


def _cache_subdir(cache_dir: Path | None) -> Path | None:
    if cache_dir is None:
        return None
    path = Path(cache_dir) / "dmbalmanac"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mark_none(cache: Path | None, key: str) -> None:
    if cache is not None:
        _write(cache / f"{key}.none", b"")


def _fresh(path: Path, ttl: timedelta | None) -> bool:
    if not path.is_file():
        return False
    if ttl is None:
        return True
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < ttl


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _fetch(url: str) -> bytes:
    """GET with the polite User-Agent and at most one request per MIN_INTERVAL.

    Raises on network or HTTP errors; callers decide how to handle that.
    """
    global _last_request
    with _throttle_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content
