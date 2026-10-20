"""The ``frameassist --check`` diagnostic report (FOV card etc.)."""

import ctypes
import sys
import types

from frameassist import app as app_mod


class _FakeVrSystem:
    def getProjectionRaw(self, eye, left, right, top, bottom):
        values = (-1.0, 1.0, 0.8, -0.8)  # 90° H, ~77.3° V monocular
        for ptr, val in zip((left, right, top, bottom), values):
            ctypes.cast(ptr, ctypes.POINTER(ctypes.c_float)).contents.value = val


def _install_fake_openvr(monkeypatch, vr_system=None):
    module = types.SimpleNamespace(
        VRSystem=lambda: vr_system or _FakeVrSystem())
    monkeypatch.setitem(sys.modules, "openvr", module)
    return module


def test_check_reports_fov_when_steamvr_reads_projection(monkeypatch, capsys):
    _install_fake_openvr(monkeypatch)
    app_mod._check({})
    out = capsys.readouterr().out
    assert "rendered FOV" in out
    assert "H≈90°" in out            # atan(1)-atan(-1)
    assert "monocular" in out


def test_check_fov_informs_when_projection_unavailable(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "openvr", types.SimpleNamespace())
    app_mod._check({})
    out = capsys.readouterr().out
    assert "rendered FOV" in out
    assert "SteamVR not reporting" in out


def test_check_runs_without_openvr_at_all(monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, "openvr", raising=False)
    # Force re-import to fail by shadowing the package name.
    monkeypatch.setitem(sys.modules, "openvr", None)
    rc = app_mod._check({})
    out = capsys.readouterr().out
    assert "rendered FOV" in out or "SteamVR" in out
    assert isinstance(rc, int)
