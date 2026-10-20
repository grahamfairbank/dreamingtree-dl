import hashlib

import pytest

from app import qbit as qmod
from app.config import QbitCfg


def bencode(v) -> bytes:
    if isinstance(v, int):
        return b"i%de" % v
    if isinstance(v, bytes):
        return b"%d:%s" % (len(v), v)
    if isinstance(v, str):
        return bencode(v.encode())
    if isinstance(v, list):
        return b"l" + b"".join(bencode(x) for x in v) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(v[k]) for k in sorted(v)) + b"e"


INFO = {"name": "show", "piece length": 16384, "pieces": b"x" * 20, "length": 5}
TORRENT = bencode({"announce": "http://t/a", "info": INFO})
EXPECTED = hashlib.sha1(bencode(INFO)).hexdigest()


class Resp:
    def __init__(self, status=200, text="", js=None):
        self.status_code, self.text, self._js = status, text, js

    def json(self):
        return self._js


class FakeSession:
    instances = []

    def __init__(self):
        self.calls = []
        self.torrents = {}
        self.authed = False
        self.expire_next = False
        self.v52 = False
        FakeSession.instances.append(self)

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        path = url.split("/api/v2/")[1]
        if path == "auth/login":
            assert kw["headers"]["Referer"]
            if kw["data"]["password"] != "pw":
                return Resp(200, "Fails.")
            self.authed = True
            return Resp(204, "") if self.v52 else Resp(200, "Ok.")
        if not self.authed or self.expire_next:
            self.expire_next = False
            self.authed = False
            return Resp(403, "Forbidden")
        if path == "app/version":
            return Resp(200, "v5.2.4")
        if path == "torrents/info":
            h = kw.get("params", {}).get("hashes")
            items = [t for t in self.torrents.values() if not h or t["hash"] == h]
            return Resp(200, js=items)
        if path == "torrents/add":
            assert kw["data"]["paused"] == "false"
            assert kw["files"]["torrents"][1] == TORRENT
            self.torrents[EXPECTED] = {"hash": EXPECTED, "state": "downloading", "progress": 0.0,
                                       "save_path": kw["data"]["savepath"]}
            self.added = kw["data"]
            return Resp(200, "Ok.")
        if path == "torrents/delete":
            self.torrents.pop(kw["data"]["hashes"], None)
            self.deleted = kw["data"]
            return Resp(200, "")
        return Resp(404)


@pytest.fixture
def q(monkeypatch):
    FakeSession.instances.clear()
    monkeypatch.setattr(qmod.requests, "Session", FakeSession)
    return qmod.Qbit(QbitCfg(url="http://qb:8080/", username="admin", password="pw", category="cat"))


def test_bdecode_roundtrip():
    assert qmod.bdecode(bencode({"a": [1, b"xy", {"b": -3}]})) == {b"a": [1, b"xy", {b"b": -3}]}
    with pytest.raises(ValueError):
        qmod.bdecode(b"i1")


def test_infohash_matches_sha1_of_info_dict():
    assert qmod.torrent_infohash(TORRENT) == EXPECTED


def test_add_info_delete(q):
    s = FakeSession.instances[0]
    h = q.add(TORRENT, "/staging/torrents", "dtdl-1")
    assert h == EXPECTED
    assert s.added["category"] == "cat" and s.added["tags"] == "dtdl-1"
    assert s.added["savepath"] == "/staging/torrents"
    assert q.info(h)["state"] == "downloading"
    assert q.add(TORRENT, "/x", "t") == h            # second add is a no-op
    assert sum(1 for c in s.calls if c[1].endswith("torrents/add")) == 1
    assert [t["hash"] for t in q.list()] == [h]
    q.delete(h, True)
    assert s.deleted["deleteFiles"] == "true"
    with pytest.raises(qmod.TorrentNotFound):
        q.info(h)


def test_relogin_on_403(q):
    s = FakeSession.instances[0]
    assert q.version() == "v5.2.4"
    s.expire_next = True
    assert q.version() == "v5.2.4"
    assert sum(1 for c in s.calls if c[1].endswith("auth/login")) == 2


def test_bad_login(monkeypatch):
    monkeypatch.setattr(qmod.requests, "Session", FakeSession)
    with pytest.raises(qmod.QbitError):
        qmod.Qbit(QbitCfg(url="http://qb", password="nope")).version()


def test_login_204_variant(q):
    FakeSession.instances[0].v52 = True
    assert q.version() == "v5.2.4"
