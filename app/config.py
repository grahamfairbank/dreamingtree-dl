"""Load, validate and save config.json.

Path to the file comes from DTDL_CONFIG (default /app/data/config.json).
Every key is documented in docs/CONFIG.md.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

DEFAULT_PATH = "/app/data/config.json"
_lock = threading.Lock()


class DreamingTreeCfg(BaseModel):
    base_url: str = "https://dreamingtree.org"
    username: str = ""
    password: str = ""


class QbitCfg(BaseModel):
    url: str = "http://qbittorrent:8080"
    username: str = "admin"
    password: str = ""
    category: str = "dreamingtree-dl"


class PathsCfg(BaseModel):
    staging: str = "/staging"          # nas:/export/dreamingtree-dl inside both containers
    library_root: str = "/library"     # media-server:/srv/music inside the app container


class Destination(BaseModel):
    label: str
    path: str                          # host-style path, e.g. /srv/music/
    # artist_code ('dmb', 'd&t', 'dm', ...) -> template; 'default' is the fallback.
    # Placeholders: {torrent_name} {album} {artist} {year}
    folder_template: dict[str, str] = Field(
        default_factory=lambda: {"dmb": "DMB_Live/{torrent_name}", "default": "Dave_Tim/{torrent_name}"}
    )


class ArtCfg(BaseModel):
    use_torrent_image: bool = True
    use_dmbalmanac: bool = True


class Config(BaseModel):
    dreamingtree: DreamingTreeCfg = DreamingTreeCfg()
    qbittorrent: QbitCfg = QbitCfg()
    paths: PathsCfg = PathsCfg()
    library_mounts: dict[str, str] = Field(default_factory=lambda: {"/srv/music/": "/library/"})
    destinations: list[Destination] = Field(
        default_factory=lambda: [Destination(label="Dave Matthews Band", path="/srv/music/")]
    )
    retention_days: int = 90
    stall_after_hours: float = 24      # no progress this long -> job shows as stalled
    abandon_after_days: float = 14     # stalled this long -> remove torrent; 0 = never give up
    art: ArtCfg = ArtCfg()
    convert_to_flac: bool = True
    post_publish_command: str = ""     # run with the library folder path as $1; empty = disabled
    post_publish_timeout_minutes: float = 120   # deep analysis of a long show takes a while
    verify_flac: bool = True           # `flac -t` every track before publishing

    # --- helpers -------------------------------------------------------
    @property
    def staging(self) -> Path:
        return Path(self.paths.staging)

    @property
    def torrents_dir(self) -> Path:
        """Untouched torrent data (seeding). qBittorrent save path root."""
        return self.staging / "torrents"

    @property
    def work_dir(self) -> Path:
        """Per-job working copies that get converted and tagged."""
        return self.staging / "work"

    def destination(self, label: str) -> Destination:
        for d in self.destinations:
            if d.label == label:
                return d
        raise KeyError(f"unknown destination {label!r}")

    def to_host_path(self, container_path: str | Path) -> str | None:
        """Inverse of to_container_path: /library/DMB_Live/x -> /srv/music/DMB_Live/x."""
        cp = str(PurePosixPath(container_path))
        for host_prefix, mount in sorted(self.library_mounts.items(), key=lambda kv: -len(kv[1])):
            m = str(PurePosixPath(mount))
            if cp == m or cp.startswith(m + "/"):
                return str(PurePosixPath(host_prefix)) + cp[len(m):]
        return None

    def to_container_path(self, host_path: str) -> Path:
        """Map a host-style destination path to where it is mounted in the container."""
        hp = str(PurePosixPath(host_path)) + "/"
        for host_prefix, mount in sorted(self.library_mounts.items(), key=lambda kv: -len(kv[0])):
            prefix = str(PurePosixPath(host_prefix)) + "/"
            if hp.startswith(prefix):
                return Path(mount) / hp[len(prefix):]
        raise ValueError(f"{host_path!r} is not under any library_mounts prefix")


def config_path() -> Path:
    return Path(os.environ.get("DTDL_CONFIG", DEFAULT_PATH))


def load(path: Path | None = None) -> Config:
    p = path or config_path()
    if not p.exists():
        return Config()
    return Config.model_validate(json.loads(p.read_text()))


def save(cfg: Config, path: Path | None = None) -> None:
    p = path or config_path()
    with _lock:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg.model_dump(), indent=2) + "\n")
        os.chmod(tmp, 0o660)  # owner + data/ group (docker-host and the app share it)
        tmp.replace(p)
