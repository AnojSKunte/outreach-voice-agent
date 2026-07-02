"""Shared test fixtures: isolated settings + throwaway SQLite per test."""

from __future__ import annotations

import pytest

from outreach.config import get_settings
from outreach.db.session import init_db, reset_db_for_tests


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Point DATABASE_URL at a temp SQLite file and rebuild the engine."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'test.db'}")
    monkeypatch.delenv("OUTREACH_API_KEY", raising=False)
    get_settings.cache_clear()
    reset_db_for_tests()
    init_db()
    yield
    get_settings.cache_clear()
    reset_db_for_tests()
