"""Shared pytest configuration.

``tmp_path`` is re-provided with a plain ``tempfile.mkdtemp`` rooted in the
workspace because on some confined sandboxes pytest's numbered-tempdir scan of
the basetemp root, and writes under the OS temp dir, are denied. This keeps
the same API for tests while working in that environment (and is a no-op
difference on Linux CI, where ``/tmp`` is writable).
"""

import os
import shutil
import uuid
from pathlib import Path

import pytest

# Root the temp dir somewhere we know we can write: right beside the tests.
_TEST_ROOT = Path(__file__).resolve().parent.parent / ".pytest-tmp"


@pytest.fixture(scope="session", autouse=True)
def _prepare_tmp_root():
    _TEST_ROOT.mkdir(parents=True, exist_ok=True)
    yield
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


@pytest.fixture
def tmp_path():
    """A writable temporary directory (same API as pytest's).

    Created explicitly (not via tempfile.mkdtemp) because on some confined
    sandboxes mkdtemp-created directories get a restrictive ACL.
    """
    path = _TEST_ROOT / f"test-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    yield path
    shutil.rmtree(path, ignore_errors=True)
