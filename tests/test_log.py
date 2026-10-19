"""Tests for log rotation: the file must never grow without bound."""

import logging
import logging.handlers

from pathlib import Path

from frameassist import log as log_mod


def test_log_uses_rotating_handler(monkeypatch, tmp_path):
    monkeypatch.setattr(log_mod, "STATE_DIR", tmp_path)
    # fresh root so setup_logging builds its handlers for this test
    root = logging.getLogger(log_mod.APP)
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    log_mod.setup_logging()
    handlers = [h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert handlers, "expected a RotatingFileHandler from setup_logging"
    h = handlers[0]
    assert 0 < h.maxBytes < 10 * 1024 * 1024
    assert h.backupCount >= 1
    assert h.baseFilename.endswith("frameassist.log")


def test_rotation_caps_total_size(tmp_path):
    from logging.handlers import RotatingFileHandler

    path = tmp_path / "frameassist.log"
    handler = RotatingFileHandler(str(path), maxBytes=2000, backupCount=3)
    logger = logging.getLogger("frameassist.test.rotate")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        line = "x" * 200 + "\n"
        for _ in range(500):
            logger.info("%s", line)
    finally:
        logger.removeHandler(handler)
        handler.close()
    files = sorted(tmp_path.glob("frameassist.log*"))
    # current + up to backupCount rotated files all stay under ~4 KB total
    total = sum(p.stat().st_size for p in files)
    assert total < 20_000, f"log total grew to {total} bytes"
    assert len(files) <= 4
