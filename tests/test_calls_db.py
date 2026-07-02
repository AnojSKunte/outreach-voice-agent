"""Call lifecycle + persistence, without any provider keys."""

from __future__ import annotations

from datetime import timedelta

from outreach import calls as call_service
from outreach.db.models import Call, Campaign, DNCEntry, Lead
from outreach.db.session import session_scope


def _mk_lead(**kw) -> str:
    with session_scope() as s:
        lead = Lead(phone=kw.pop("phone", "+919876543210"), **kw)
        s.add(lead)
        s.flush()
        return lead.id


def test_finalize_call_persists_transcript_and_duration(fresh_db):
    lead_id = _mk_lead()
    call_id = call_service.create_call(
        direction="outbound", agent_id="lead-gen-demo", to_number="+919876543210", lead_id=lead_id
    )
    call_service.mark_started(call_id, profile="budget")

    transcript = [
        {"role": "assistant", "content": "Namaste!", "t": "2026-07-02T10:00:00Z"},
        {"role": "user", "content": "Haan boliye", "t": "2026-07-02T10:00:05Z"},
    ]
    # No LLM keys in tests -> analysis is skipped gracefully.
    result = call_service.finalize_call(call_id, transcript)

    assert result["status"] == "completed"
    with session_scope() as s:
        call = s.get(Call, call_id)
        assert call.transcript == transcript
        assert call.duration_seconds is not None and call.duration_seconds >= 0
        assert call.profile == "budget"
        assert call.estimated_cost_usd is None or call.estimated_cost_usd >= 0
        lead = s.get(Lead, lead_id)
        assert lead.status == "contacted"  # completed call, no analysis outcome


def test_dnc_outcome_adds_to_dnc_list(fresh_db):
    lead_id = _mk_lead(phone="+911112223334")
    call_id = call_service.create_call(
        direction="outbound", agent_id="x", to_number="+911112223334", lead_id=lead_id
    )
    call_service.mark_started(call_id)
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        call_service._apply_outcome_to_lead(s, lead, "completed", "dnc_request")
    with session_scope() as s:
        assert s.get(Lead, lead_id).status == "dnc"
        assert s.get(DNCEntry, "+911112223334") is not None


def test_missed_call_schedules_retry_then_exhausts(fresh_db):
    with session_scope() as s:
        camp = Campaign(name="t", agent_id="x", max_attempts_per_lead=2, retry_minutes=60)
        s.add(camp)
        s.flush()
        camp_id = camp.id
    lead_id = _mk_lead(campaign_id=camp_id, attempts=1, status="calling")

    call_id = call_service.create_call(
        direction="outbound", agent_id="x", to_number="+919876543210",
        lead_id=lead_id, campaign_id=camp_id,
    )
    call_service.handle_missed_outbound(call_id, "no_answer")
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        assert lead.status == "queued"
        assert lead.next_attempt_at is not None
        delta = call_service.as_aware(lead.next_attempt_at) - call_service.utcnow()
        assert timedelta(minutes=55) < delta < timedelta(minutes=65)

    # Second miss at the attempt cap -> unreachable.
    with session_scope() as s:
        s.get(Lead, lead_id).attempts = 2
    call_id2 = call_service.create_call(
        direction="outbound", agent_id="x", to_number="+919876543210",
        lead_id=lead_id, campaign_id=camp_id,
    )
    call_service.handle_missed_outbound(call_id2, "no_answer")
    with session_scope() as s:
        assert s.get(Lead, lead_id).status == "unreachable"