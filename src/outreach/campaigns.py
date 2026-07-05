"""Campaign engine: bulk outbound dialing with guardrails.

An asyncio loop inside the web service (no extra worker to deploy) that,
every few seconds, for each *running* campaign:

1. Checks the calling window (hours + days, in the campaign's timezone).
2. Counts in-flight calls and dials up to ``max_concurrent_calls``.
3. Picks due leads: status 'new'/'queued'/'callback', past ``next_attempt_at``,
   under ``max_attempts_per_lead``, and NOT on the DNC list.
4. Originates the call via the active carrier with the campaign's context.

Retries for unanswered calls are scheduled by the call lifecycle service
(``handle_missed_outbound``); this loop just respects ``next_attempt_at``.

Compliance note: honoring DNC, capping attempts, and calling only inside a
sane local-time window are not optional extras — they're what keeps a
client's numbers from being blocked (TRAI/UCC in India, TCPA in the US).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import func, or_, select

from outreach import calls as call_service
from outreach import events
from outreach.db.models import Call, Campaign, DNCEntry, Lead
from outreach.db.session import session_scope
from outreach.telephony import TelephonyError, get_carrier

TICK_SECONDS = 5


def in_calling_window(campaign: Campaign, now: datetime | None = None) -> bool:
    try:
        tz = ZoneInfo(campaign.timezone or "Asia/Kolkata")
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")
    local = (now or datetime.now(tz)).astimezone(tz)
    if str(local.weekday()) not in (campaign.calling_days or "0123456"):
        return False
    return campaign.calling_hours_start <= local.hour < campaign.calling_hours_end


def _due_leads(s, campaign: Campaign, limit: int) -> list[Lead]:
    now = call_service.utcnow()
    dnc_phones = select(DNCEntry.phone)
    stmt = (
        select(Lead)
        .where(
            Lead.campaign_id == campaign.id,
            Lead.status.in_(("new", "queued", "callback")),
            Lead.attempts < campaign.max_attempts_per_lead,
            or_(Lead.next_attempt_at.is_(None), Lead.next_attempt_at <= now),
            Lead.phone.not_in(dnc_phones),
        )
        .order_by(Lead.next_attempt_at.asc().nulls_first(), Lead.created_at.asc())
        .limit(limit)
    )
    return list(s.scalars(stmt))


def _in_flight_count(s, campaign_id: str) -> int:
    stmt = select(func.count(Call.id)).where(
        Call.campaign_id == campaign_id,
        Call.status.in_(("initiated", "ringing", "in_progress")),
    )
    return int(s.scalar(stmt) or 0)


def dial_lead(lead_id: str, campaign_id: str | None = None) -> str:
    """Originate one outbound call to a lead (used by the engine AND the
    'call this lead now' API). Returns the new call id."""
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if lead is None:
            raise ValueError(f"unknown lead {lead_id}")
        if s.get(DNCEntry, lead.phone):
            lead.status = "dnc"
            raise ValueError(f"lead {lead_id} phone is on the DNC list")
        campaign = s.get(Campaign, campaign_id or lead.campaign_id) if (campaign_id or lead.campaign_id) else None
        agent_id = campaign.agent_id if campaign else None
        if not agent_id:
            from outreach.config import get_settings

            agent_id = get_settings().default_agent_id
        lead.status = "calling"
        lead.attempts += 1
        lead.last_attempt_at = call_service.utcnow()
        phone = lead.phone
        camp_id = campaign.id if campaign else None

    call_id = call_service.create_call(
        direction="outbound",
        agent_id=agent_id,
        to_number=phone,
        lead_id=lead_id,
        campaign_id=camp_id,
    )
    try:
        # Carrier construction validates credentials — inside the try so a
        # misconfigured deployment produces visible 'failed' calls with a
        # clear error (and normal retry/backoff) instead of a hot loop.
        carrier = get_carrier()
        from outreach.config import get_settings as _gs

        provider_id = carrier.originate_call(
            phone,
            agent_id=agent_id,
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=camp_id,
            detect_voicemail=_gs().amd_enabled,
        )
        call_service.set_provider_call_id(call_id, provider_id)
    except (TelephonyError, RuntimeError) as exc:
        logger.error(f"dial-out failed for lead {lead_id}: {exc}")
        call_service.update_status(call_id, "failed", error=str(exc))
        call_service.handle_missed_outbound(call_id, "failed")
        raise TelephonyError(str(exc)) from exc
    return call_id


async def _tick() -> None:
    with session_scope() as s:
        running = list(s.scalars(select(Campaign).where(Campaign.status == "running")))

    for campaign in running:
        if not in_calling_window(campaign):
            continue
        with session_scope() as s:
            camp = s.get(Campaign, campaign.id)
            in_flight = _in_flight_count(s, campaign.id)
            slots = max(0, camp.max_concurrent_calls - in_flight)
            if slots == 0:
                continue
            leads = _due_leads(s, camp, slots)
            # Campaign done? No dialable leads now or ever, nothing in flight.
            if not leads and in_flight == 0:
                remaining = s.scalar(
                    select(func.count(Lead.id)).where(
                        Lead.campaign_id == camp.id,
                        Lead.status.in_(("new", "queued", "callback", "calling")),
                        Lead.attempts < camp.max_attempts_per_lead,
                    )
                )
                if not remaining:
                    camp.status = "completed"
                    logger.info(f"campaign '{camp.name}' completed")
                    events.emit_soon("campaign.completed", {"campaign_id": camp.id})
                continue
            lead_ids = [lead.id for lead in leads]

        for lead_id in lead_ids:
            try:
                # Carrier REST is blocking; keep the loop responsive.
                await asyncio.to_thread(dial_lead, lead_id, campaign.id)
            except Exception as exc:
                logger.warning(f"campaign {campaign.id}: dial {lead_id} failed: {exc}")


async def run_campaign_loop(stop: asyncio.Event | None = None) -> None:
    """Run forever (until ``stop`` is set). Started by the server on boot."""
    logger.info("campaign engine started")
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            await _tick()
        except Exception:
            logger.exception("campaign tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("campaign engine stopped")
