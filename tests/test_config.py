"""Tests for config: loading, defaults, validation, words."""

import json

from frameassist import config as c


def test_defaults_when_missing(tmp_path):
    cfg = c.load(tmp_path / "missing.json")
    assert cfg["model"] == "base.en"
    assert cfg["add_space"] is True
    assert cfg["after_talking"] == "type"
    assert cfg["ai_key"] == ""


def test_saved_values_win(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "small.en", "add_space": False,
                                "after_talking": "clipboard"}))
    cfg = c.load(path)
    assert cfg["model"] == "small.en"
    assert cfg["add_space"] is False
    assert cfg["after_talking"] == "clipboard"


def test_bad_values_repaired(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "huge.en", "after_talking": "launch"}))
    cfg = c.load(path)
    assert cfg["model"] == "base.en"
    assert cfg["after_talking"] == "type"


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"some_new_key": 42}))
    cfg = c.load(path)
    assert "some_new_key" not in cfg


def test_save_round_trip(tmp_path):
    path = tmp_path / "config.json"
    c.save({"model": "tiny.en", "add_space": True}, path)
    assert json.loads(path.read_text())["model"] == "tiny.en"


def test_words(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("# comment\nAlex\nSam, Sam\n  GameX  \n")
    assert c.load_words(path) == ["Alex", "Sam", "GameX"]


def test_words_file_creates_with_header(tmp_path):
    path = tmp_path / "words.txt"
    got = c.words_file(path)
    assert got == path
    assert path.read_text().startswith("# My words")


def test_remove_desktop_autostart(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "AUTOSTART_FILE", tmp_path / "autostart.desktop")
    (tmp_path / "autostart.desktop").write_text("x")
    c.remove_desktop_autostart()
    assert not (tmp_path / "autostart.desktop").exists()


# -- streamed-PC delivery mode (opt-in, additive) ---------------------------

def test_delivery_and_host_defaults_present(tmp_path):
    cfg = c.load(tmp_path / "missing.json")
    assert cfg["delivery"] == "frame"
    assert cfg["host_addr"] == ""
    assert cfg["host_port"] == 8763
    assert cfg["host_secret"] == ""


def test_delivery_host_accepted(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"delivery": "host", "host_addr": "192.168.1.50",
                                "host_secret": "ABCD-1234"}))
    cfg = c.load(path)
    assert cfg["delivery"] == "host"
    assert cfg["host_addr"] == "192.168.1.50"
    assert cfg["host_secret"] == "ABCD-1234"


def test_bad_delivery_repaired_to_default(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"delivery": "telepathy"}))
    cfg = c.load(path)
    assert cfg["delivery"] == "frame"


def test_host_port_coerced_to_int(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"host_port": "9001"}))
    cfg = c.load(path)
    assert cfg["host_port"] == 9001
    assert isinstance(cfg["host_port"], int)


def test_host_port_bad_value_repaired(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"host_port": "not-a-port"}))
    cfg = c.load(path)
    assert cfg["host_port"] == c.DEFAULTS["host_port"]
