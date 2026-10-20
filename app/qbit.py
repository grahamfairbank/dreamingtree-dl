"""Thin qBittorrent Web API v2 client.

Authentication is cookie based; the session logs in lazily and re-logs-in once on HTTP 403.
`add` computes the v1 infohash itself (SHA1 of the bencoded info dict) so callers get the hash
without polling the client for it.
"""
from __future__ import annotations

import json
import hashlib
import threading

import requests

from .config import QbitCfg


class QbitError(RuntimeError):
    pass


class TorrentNotFound(QbitError):
    pass


# --- minimal bencode decoder --------------------------------------------------------------

def _bdecode(data: bytes, i: int = 0):
    """Decode one bencoded value starting at i; return (value, next_index)."""
    c = data[i:i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1:end]), end + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = _bdecode(data, i)
            v, i = _bdecode(data, i)
            out[k] = v
        return out, i + 1
    if c.isdigit():
        colon = data.index(b":", i)
        n = int(data[i:colon])
        start = colon + 1
        if start + n > len(data):
            raise ValueError("truncated bencode string")
        return data[start:start + n], start + n
    raise ValueError(f"invalid bencode at offset {i}")


def bdecode(data: bytes):
    value, end = _bdecode(data, 0)
    if end != len(data):
        raise ValueError("trailing data after bencode value")
    return value


def torrent_infohash(torrent: bytes) -> str:
    """SHA1 (hex) of the raw bencoded `info` dict. Hashes the original bytes, not a re-encoding."""
    if torrent[:1] != b"d":
        raise ValueError("not a torrent file")
    i = 1
    while torrent[i:i + 1] != b"e":
        key, i = _bdecode(torrent, i)
        start = i
        _, i = _bdecode(torrent, i)
        if key == b"info":
            return hashlib.sha1(torrent[start:i]).hexdigest()
    raise ValueError("torrent has no info dict")


# --- client -----------------------------------------------------------------------------

class Qbit:
    def __init__(self, cfg: QbitCfg, timeout: float = 30.0):
        self.cfg = cfg
        self.base = cfg.url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._lock = threading.RLock()
        self._logged_in = False

    # -- plumbing
    def login(self) -> None:
        with self._lock:
            r = self._session.request(
                "POST", f"{self.base}/api/v2/auth/login",
                data={"username": self.cfg.username, "password": self.cfg.password},
                headers={"Referer": self.base}, timeout=self.timeout,
            )
            # qBittorrent <5.2 answers 200 "Ok."/"Fails."; 5.2 answers 204 on success and 401/403 on failure
            ok = r.status_code == 204 or (r.status_code == 200 and r.text.strip() == "Ok.")
            if not ok:
                self._logged_in = False
                raise QbitError(f"qBittorrent login failed (HTTP {r.status_code})")
            self._logged_in = True

    def _request(self, method: str, path: str, **kw):
        with self._lock:
            if not self._logged_in:
                self.login()
            kw.setdefault("timeout", self.timeout)
            kw.setdefault("headers", {"Referer": self.base})
            url = f"{self.base}/api/v2/{path}"
            r = self._session.request(method, url, **kw)
            if r.status_code == 403:
                self.login()
                r = self._session.request(method, url, **kw)
            if not 200 <= r.status_code < 300:
                raise QbitError(f"qBittorrent {path} failed: HTTP {r.status_code}")
            return r

    # -- API
    # Applied at app start. Dead (0-seeder) torrents must not hold the active-download slots,
    # or one stalled show blocks every download queued after it.
    REQUIRED_PREFS = {
        "max_active_downloads": 8, "max_active_torrents": -1, "max_active_uploads": -1,
        "dont_count_slow_torrents": True, "slow_torrent_dl_rate_threshold": 2,
        "slow_torrent_ul_rate_threshold": 2, "slow_torrent_inactive_timer": 120,
        "max_ratio_enabled": False, "max_seeding_time_enabled": False,
    }

    def set_preferences(self, prefs: dict) -> None:
        self._request("POST", "app/setPreferences", data={"json": json.dumps(prefs)})

    def ensure_preferences(self) -> None:
        self.set_preferences(self.REQUIRED_PREFS)

    def version(self) -> str:
        return self._request("GET", "app/version").text.strip()

    def list(self, category: str | None = None) -> list[dict]:
        cat = self.cfg.category if category is None else category
        params = {"category": cat} if cat else {}
        return self._request("GET", "torrents/info", params=params).json()

    def info(self, infohash: str) -> dict:
        """The torrents/info entry (state, progress, content_path, save_path, completion_on, ...)."""
        items = self._request("GET", "torrents/info", params={"hashes": infohash.lower()}).json()
        for it in items:
            if it.get("hash", "").lower() == infohash.lower():
                return it
        raise TorrentNotFound(infohash)

    def add(self, torrent: bytes, save_path: str, tag: str) -> str:
        """Add a torrent (started, not paused) and return its infohash. Re-adding is harmless."""
        infohash = torrent_infohash(torrent)
        try:
            self.info(infohash)
            return infohash                       # already present
        except TorrentNotFound:
            pass
        data = {"savepath": save_path, "tags": tag, "paused": "false", "stopped": "false"}
        if self.cfg.category:
            data["category"] = self.cfg.category
        r = self._request("POST", "torrents/add", data=data,
                          files={"torrents": ("job.torrent", torrent, "application/x-bittorrent")})
        if r.text.strip().lower().startswith("fail"):
            raise QbitError("qBittorrent rejected the torrent")
        return infohash

    def delete(self, infohash: str, delete_files: bool) -> None:
        self._request("POST", "torrents/delete",
                      data={"hashes": infohash.lower(), "deleteFiles": "true" if delete_files else "false"})
