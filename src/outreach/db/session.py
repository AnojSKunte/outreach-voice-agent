"""Engine/session management.

A single module-level engine (created lazily from settings) plus a
``session_scope`` context manager used by the API, the campaign engine and
the call pipeline. Tests point ``DATABASE_URL`` at a temp SQLite file.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from outreach.config import get_settings
from outreach.db.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _build_engine() -> Engine:
    url = get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # Allow use across threads (FastAPI + background campaign loop).
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def init_db(engine: Engine | None = None) -> Engine:
    """Create the engine (once) and all tables. Safe to call repeatedly."""
    global _engine, _SessionLocal
    if engine is not None:
        _engine = engine
    if _engine is None:
        _engine = _build_engine()
    if _SessionLocal is None or engine is not None:
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    return _engine


def reset_db_for_tests() -> None:
    """Drop cached engine/session so tests can re-point DATABASE_URL."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, rollback on error, always close."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
