"""Persistence: leads, calls, campaigns, DNC.

SQLite by default (zero-config for local + small deployments); set
``DATABASE_URL`` to a Postgres URL in production — the models are plain
SQLAlchemy 2.0 and work on both.
"""

from outreach.db.models import Base, Call, Campaign, DNCEntry, Lead
from outreach.db.session import get_session, init_db, session_scope

__all__ = [
    "Base",
    "Lead",
    "Call",
    "Campaign",
    "DNCEntry",
    "init_db",
    "get_session",
    "session_scope",
]
