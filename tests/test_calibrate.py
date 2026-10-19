"""Tests for the Calibrate module: session navigation, voice commands,
profiles, and (when Qt is available) chart rendering."""

import pytest

from frameassist import calibrate as cal


def test_session_starts_on_resolution():
    s = cal.CalibrationSession()
    s.start()
    assert s.active
    assert s.test == "resolution"
    assert s.fineness == 1


def test_next_cycles_through_tests():
    s = cal.CalibrationSession()
    s.start()
    s.next()
    assert s.test == "alignment"
    s.next()
    assert s.test == "sweetspot"
    s.next()  # wraps
    assert s.test == "resolution"


def test_prev_wraps_backwards():
    s = cal.CalibrationSession()
    s.start()
    s.prev()
    assert s.test == "sweetspot"


def test_finer_coarser_bounds():
    s = cal.CalibrationSession()
    s.start()
    for _ in range(10):
        s.finer()
    assert s.fineness == cal.FINENESS_LEVELS - 1
    for _ in range(10):
        s.coarser()
    assert s.fineness == 0


def test_status_text():
    s = cal.CalibrationSession()
    s.start()
    assert "Resolution" in s.status()
    assert "fine" in s.status()


def test_stop():
    s = cal.CalibrationSession()
    s.start()
    s.stop()
    assert not s.active


# -- voice commands ----------------------------------------------------------
def test_voice_next_prev():
    s = cal.CalibrationSession()
    s.start()
    cmd, _ = s.interpret("show the next chart")
    assert cmd == "next"
    assert s.test == "alignment"
    cmd, _ = s.interpret("go back")
    assert cmd == "prev"
    assert s.test == "resolution"


def test_voice_finer_coarser():
    s = cal.CalibrationSession()
    s.start()
    s.interpret("make it finer")
    assert s.fineness == 2
    s.interpret("coarser")
    assert s.fineness == 1


def test_voice_save_extracts_name():
    s = cal.CalibrationSession()
    s.start()
    cmd, detail = s.interpret("save as with spacers")
    assert cmd == "save"
    assert detail == "with spacers"


def test_voice_done_stops():
    s = cal.CalibrationSession()
    s.start()
    cmd, _ = s.interpret("that's good, stop")
    assert cmd == "stop"
    assert not s.active


def test_voice_unknown_is_none():
    s = cal.CalibrationSession()
    s.start()
    cmd, _ = s.interpret("hello world")
    assert cmd == "none"


# -- profiles ----------------------------------------------------------------
def test_profiles_round_trip(tmp_path):
    path = tmp_path / "profiles.json"
    cal.save_profile("with-spacers", test="alignment", fineness=2, path=path)
    cal.save_profile("default", test="resolution", fineness=0, path=path)
    profiles = cal.load_profiles(path)
    assert [p["name"] for p in profiles] == ["with-spacers", "default"]
    assert profiles[0]["fineness"] == 2


def test_profile_save_overwrites_same_name(tmp_path):
    path = tmp_path / "profiles.json"
    cal.save_profile("a", test="resolution", path=path)
    cal.save_profile("a", test="sweetspot", path=path)
    profiles = cal.load_profiles(path)
    assert len(profiles) == 1
    assert profiles[0]["test"] == "sweetspot"


def test_profile_remove(tmp_path):
    path = tmp_path / "profiles.json"
    cal.save_profile("a", path=path)
    cal.save_profile("b", path=path)
    cal.remove_profile("a", path=path)
    assert [p["name"] for p in cal.load_profiles(path)] == ["b"]


def test_profiles_round_trip_missing_file(tmp_path):
    assert cal.load_profiles(tmp_path / "none.json") == []


# -- rendering (Qt available) ------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    """A real QApplication so drawText has a font database (offscreen)."""
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_render_each_chart_returns_texture(qapp):
    import pytest
    pytest.importorskip("PySide6")
    for test in cal.TESTS:
        buf = cal.render_chart(test, fineness=1)
        assert len(buf) == cal.W * cal.H * 4


def test_render_invalid_chart_raises(qapp):
    import pytest
    pytest.importorskip("PySide6")
    with pytest.raises(ValueError):
        cal.render_chart("bogus")
