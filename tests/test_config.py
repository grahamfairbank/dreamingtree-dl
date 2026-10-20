import json
from app import config


def test_example_config_loads_and_maps_paths(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(open("config.example.json").read())
    cfg = config.load(p)
    assert cfg.retention_days == 90
    dest = cfg.destination("Dave Matthews Band")
    assert cfg.to_container_path(dest.path) == config.Path("/library")
    assert cfg.to_container_path("/srv/music/DMB_Live") == config.Path("/library/DMB_Live")


def test_save_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    cfg = config.Config()
    cfg.retention_days = 30
    config.save(cfg, p)
    assert json.loads(p.read_text())["retention_days"] == 30
    assert (p.stat().st_mode & 0o777) == 0o660


def test_to_host_path_inverts_library_mounts():
    cfg = config.Config()
    assert cfg.to_host_path("/library/DMB_Live/x y") == "/srv/music/DMB_Live/x y"
    assert cfg.to_host_path("/elsewhere/x") is None
