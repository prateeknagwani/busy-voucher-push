"""Isolates every test from the REAL production sqlite stores.

Real bug hit live (2026-09-18): every test file was deleting/reseeding
app/idempotency.py's DB_PATH directly -- the actual file the running
server also reads. Running `pytest` corrupted the live voucher-number
counter as a side effect, which then silently undid a manual fix and
caused a confusing "duplicate voucher number" failure against the live
server that had nothing to do with the code itself. Tests must NEVER touch
the real data/*.sqlite files -- redirect both idempotency and settings
DB_PATH to a fresh temp file per test instead.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import idempotency, settings_store


@pytest.fixture(autouse=True)
def isolated_sqlite_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(idempotency, "DB_PATH", tmp_path / "idempotency.sqlite")
    monkeypatch.setattr(settings_store, "DB_PATH", tmp_path / "settings.sqlite")
    yield
