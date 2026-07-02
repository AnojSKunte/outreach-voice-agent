"""Campaign engine policies: calling windows, due-lead selection, DNC."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from outreach.campaigns import _due_leads, in_calling_window
from outreach.db.models import Campaign, DNCEntry, Lead
from outreach.db.session import session_scope


def _campaign(**kw) -> Campaign:
    defaults = dict(
        name="test", agent_id="lead-gen-demo", status="running",
        calling_hours_start=10, calling_hours_end=19,
        timezone="Asia/Kolkata", calling_days="012345",
    )
    defaults.update(kw)
    return Campaign(**defaults)


def test_calling_window_respects_hours_and_days(fresh_db):
    camp = _campaign()
    tz = ZoneInfo("Asia/Kolkata")
    # Wednesday 14:00 IST -> inside window
    assert in_calling_window(camp, datetime(2026, 7, 1, 14, 0, tzinfo=tz))
    # Wednesday 08:00 IST -> too early
    assert not in_calling_window(camp, datetime(2026, 7, 1, 8, 0, tzinfo=tz))
    # Wednesday 19:00 IST -> window end is exclusive
    assert not in_calling_window(camp, datetime(2026, 7, 1, 19, 0, tzinfo=tz))
    # Sunday (weekday 6) not in "012345"
    assert not in_calling_window(camp, datetime(2026, 7, 5, 14, 0, tzinfo=tz))


def test_due_leads_skips_dnc_and_exhausted(fresh_db):
    with session_scope() as s:
        camp = _campaign(max_attempts_per_lead=2)
        s.add(camp)
        s.flush()
        s.add_all(
            [
                Lead(phone="+911", status="new", campaign_id=camp.id),
                Lead(phone="+912", status="queued", campaign_id=camp.id),
                Lead(phone="+913", status="new", campaign_id=camp.id),   # DNC below
                Lead(phone="+914", status="queued", attempts=2, campaign_id=camp.id),  # exhausted
                Lead(phone="+915", status="contacted", campaign_id=camp.id),  # not dialable
            ]
        )
        s.add(DNCEntry(phone="+913"))
        s.flush()
        due = _due_leads(s, camp, limit=10)
        phones = {lead.phone for lead in due}
    assert phones == {"+911", "+912"}
