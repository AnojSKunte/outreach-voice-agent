"""Call lifecycle service: the single place call state changes happen.

Everything that needs to happen around a call — creating the DB record,
status updates from carrier callbacks, finalizing with transcript + analysis,
updating the lead, firing webhooks — lives here so the server, campaign
engine and API all share one code path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from outreach import events
from outreach.analysis import analyze_transcript
from outreach.db.models import Call, DNCEntry, Lead
from outreach.db.session import session_scope
from outreach.providers.profiles import estimate_cost_per_minute


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes even for timezone=True columns;
    normalise to UTC-aware before doing arithmetic."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# creation / status
# ----------------------------------------------------------------------

def create_call(
    *,
    direction: str,
    agent_id: str,
    to_number: str | None = None,
    from_number: str | None = None,
    lead_id: str | None = None,
    campaign_id: str | None = None,
    provider_call_id: str | None = None,
    status: str = "initiated",
) -> str:
    with session_scope() as s:
        call = Call(
            direction=direction,
            agent_id=agent_id,
            to_number=to_number,
            from_number=from_number,
            lead_id=lead_id,
            campaign_id=campaign_id,
            provider_call_id=provider_call_id,
            status=status,
        )
        s.add(call)
        s.flush()
        call_id = call.id
    return call_id


def set_provider_call_id(call_id: str, provider_call_id: str) -> None:
    with session_scope() as s:
        call = s.get(Call, call_id)
        if call:
            call.provider_call_id = provider_call_id


def update_status(call_id: str, status: str, error: str | None = None) -> None:
    with session_scope() as s:
        call = s.get(Call, call_id)
        if not call:
            logger.warning(f"status update for unknown call {call_id}")
            return
        call.status = status
        if error:
            call.error = error
        if status == "in_progress" and call.started_at is None:
            call.started_at = utcnow()
        if status in ("completed", "failed", "no_answer", "busy", "canceled", "voicemail"):
            if call.ended_at is None:
                call.ended_at = utcnow()
    if status == "in_progress":
        events.emit_soon("call.started", {"call_id": call_id})


def mark_started(call_id: str, profile: str | None = None) -> None:
    with session_scope() as s:
        call = s.get(Call, call_id)
        if not call:
            return
        call.status = "in_progress"
        call.started_at = call.started_at or utcnow()
        if profile:
            call.profile = profile
    events.emit_soon("call.started", {"call_id": call_id})


# ----------------------------------------------------------------------
# finalization
# ----------------------------------------------------------------------

def finalize_call(
    call_id: str,
    transcript: list[dict[str, Any]] | None,
    status: str = "completed",
) -> dict[str, Any]:
    """Persist the transcript, compute duration/cost, run post-call analysis,
    update the lead, fire webhooks. Returns the final call dict."""
    analysis = analyze_transcript(transcript or [])

    with session_scope() as s:
        call = s.get(Call, call_id)
        if not call:
            logger.warning(f"finalize for unknown call {call_id}")
            return {}

        call.transcript = transcript or []
        if call.ended_at is None:
            call.ended_at = utcnow()
        if call.started_at and call.ended_at:
            call.duration_seconds = max(
                0,
                int((as_aware(call.ended_at) - as_aware(call.started_at)).total_seconds()),
            )
        # A call that connected and has a transcript completed normally.
        call.status = status
        if call.duration_seconds and call.profile:
            call.estimated_cost_usd = round(
                (call.duration_seconds / 60.0) * estimate_cost_per_minute(call.profile), 4
            )
        if analysis:
            call.summary = analysis["summary"]
            call.outcome = analysis["outcome"]
            call.extracted = analysis["extracted"]

        result = call.as_dict()
        lead_id = call.lead_id
        outcome = call.outcome

        # --- lead bookkeeping ---
        if lead_id:
            lead = s.get(Lead, lead_id)
            if lead:
                _apply_outcome_to_lead(s, lead, status, outcome)

    events.emit_soon("call.ended", {"call_id": call_id, "status": status})
    if analysis:
        events.emit_soon(
            "call.analyzed",
            {"call_id": call_id, "summary": analysis["summary"], "outcome": analysis["outcome"]},
        )
    return result


def _apply_outcome_to_lead(s, lead: Lead, call_status: str, outcome: str | None) -> None:
    """Translate a finished call into the lead's next state."""
    lead.last_attempt_at = utcnow()

    if outcome == "dnc_request":
        lead.status = "dnc"
        if not s.get(DNCEntry, lead.phone):
            s.add(DNCEntry(phone=lead.phone, reason="requested during call"))
        return
    if outcome == "converted":
        lead.status = "converted"
        return
    if outcome == "interested":
        lead.status = "interested"
        return
    if outcome == "not_interested":
        lead.status = "not_interested"
        return
    if outcome == "callback":
        lead.status = "callback"
        # Try again in 24h unless analysis extracted a specific time.
        lead.next_attempt_at = utcnow() + timedelta(hours=24)
        return

    if call_status == "completed":
        lead.status = "contacted"
    # no_answer / busy / voicemail / failed -> retry policy is applied by the
    # campaign engine (it knows max_attempts and retry_minutes).


def handle_missed_outbound(call_id: str, status: str) -> None:
    """Carrier says the call never became a conversation (no answer, busy,
    voicemail-hangup, failed). Schedule the retry via the campaign policy."""
    from outreach.db.models import Campaign  # local import to avoid cycles

    with session_scope() as s:
        call = s.get(Call, call_id)
        if not call:
            return
        call.status = status
        call.ended_at = call.ended_at or utcnow()
        if not call.lead_id:
            return
        lead = s.get(Lead, call.lead_id)
        if not lead:
            return
        lead.last_attempt_at = utcnow()
        campaign = s.get(Campaign, lead.campaign_id) if lead.campaign_id else None
        max_attempts = campaign.max_attempts_per_lead if campaign else 3
        retry_minutes = campaign.retry_minutes if campaign else 240
        if lead.attempts >= max_attempts:
            lead.status = "unreachable"
        else:
            lead.status = "queued"
            lead.next_attempt_at = utcnow() + timedelta(minutes=retry_minutes)
    events.emit_soon("call.ended", {"call_id": call_id, "status": status})
