"""REST API (v1) — everything the dashboard does, scriptable.

Auth: set OUTREACH_API_KEY and send it as ``X-API-Key``. If unset, the API
is open (local development only).

Design: thin routes over the same services the engine uses (``campaigns``,
``calls``); no business logic lives here.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from outreach.config import get_settings
from outreach.db.models import Call, Campaign, DNCEntry, Lead
from outreach.db.session import session_scope

router = APIRouter(prefix="/api/v1")


# ----------------------------------------------------------------------
# auth
# ----------------------------------------------------------------------

def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().outreach_api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


dep = [Depends(require_api_key)]


# ----------------------------------------------------------------------
# agents (read-only: they live as YAML on disk, by design)
# ----------------------------------------------------------------------

@router.get("/agents", dependencies=dep)
def list_agents() -> list[dict[str, Any]]:
    from outreach.agents import get_registry

    reg = get_registry()
    out = []
    for agent_id in reg.agent_ids:
        a = reg.by_id(agent_id)
        out.append(
            {
                "agent_id": a.agent_id,
                "client_name": a.client_name,
                "phone_number": a.phone_number,
                "profile": a.profile or get_settings().default_profile,
                "persona": a.persona.model_dump(),
                "allowed_actions": a.allowed_actions,
                "language": a.persona.language,
            }
        )
    return out


# ----------------------------------------------------------------------
# leads
# ----------------------------------------------------------------------

class LeadIn(BaseModel):
    phone: str
    name: str | None = None
    company: str | None = None
    email: str | None = None
    notes: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)
    campaign_id: str | None = None


@router.post("/leads", dependencies=dep)
def create_leads(leads: list[LeadIn] | LeadIn) -> dict[str, Any]:
    items = leads if isinstance(leads, list) else [leads]
    created = []
    with session_scope() as s:
        for item in items:
            lead = Lead(**item.model_dump())
            s.add(lead)
            s.flush()
            created.append(lead.id)
    return {"created": len(created), "ids": created}


@router.post("/leads/import", dependencies=dep)
async def import_leads_csv(
    file: UploadFile, campaign_id: str | None = Query(default=None)
) -> dict[str, Any]:
    """CSV import. Columns: phone (required), name, company, email, notes —
    anything else lands in ``custom`` and is visible to the agent on calls."""
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    known = {"phone", "name", "company", "email", "notes"}
    created, skipped = 0, 0
    with session_scope() as s:
        for row in reader:
            row = { (k or "").strip().lower(): (v or "").strip() for k, v in row.items() }
            phone = row.get("phone", "")
            if not phone:
                skipped += 1
                continue
            custom = {k: v for k, v in row.items() if k not in known and v}
            s.add(
                Lead(
                    phone=phone,
                    name=row.get("name") or None,
                    company=row.get("company") or None,
                    email=row.get("email") or None,
                    notes=row.get("notes") or None,
                    custom=custom,
                    campaign_id=campaign_id,
                )
            )
            created += 1
    return {"created": created, "skipped_no_phone": skipped}


@router.get("/leads", dependencies=dep)
def list_leads(
    status: str | None = None,
    campaign_id: str | None = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
) -> list[dict[str, Any]]:
    with session_scope() as s:
        stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit).offset(offset)
        if status:
            stmt = stmt.where(Lead.status == status)
        if campaign_id:
            stmt = stmt.where(Lead.campaign_id == campaign_id)
        return [lead.as_dict() for lead in s.scalars(stmt)]


@router.patch("/leads/{lead_id}", dependencies=dep)
def update_lead(lead_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "company", "email", "notes", "custom", "status", "campaign_id", "phone"}
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "lead not found")
        for k, v in patch.items():
            if k in allowed:
                setattr(lead, k, v)
        return lead.as_dict()


@router.delete("/leads/{lead_id}", dependencies=dep)
def delete_lead(lead_id: str) -> dict[str, str]:
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "lead not found")
        s.delete(lead)
    return {"deleted": lead_id}


# ----------------------------------------------------------------------
# campaigns
# ----------------------------------------------------------------------

class CampaignIn(BaseModel):
    name: str
    agent_id: str
    goal: str = ""
    max_concurrent_calls: int = 3
    max_attempts_per_lead: int = 3
    retry_minutes: int = 240
    calling_hours_start: int = 10
    calling_hours_end: int = 19
    timezone: str = "Asia/Kolkata"
    calling_days: str = "012345"


@router.post("/campaigns", dependencies=dep)
def create_campaign(body: CampaignIn) -> dict[str, Any]:
    from outreach.agents import get_registry

    if body.agent_id not in get_registry().agent_ids:
        raise HTTPException(400, f"unknown agent_id '{body.agent_id}'")
    with session_scope() as s:
        c = Campaign(**body.model_dump())
        s.add(c)
        s.flush()
        return c.as_dict()


@router.get("/campaigns", dependencies=dep)
def list_campaigns() -> list[dict[str, Any]]:
    with session_scope() as s:
        out = []
        for c in s.scalars(select(Campaign).order_by(Campaign.created_at.desc())):
            d = c.as_dict()
            d["stats"] = _campaign_stats(s, c.id)
            out.append(d)
        return out


def _campaign_stats(s, campaign_id: str) -> dict[str, Any]:
    by_status = dict(
        s.execute(
            select(Lead.status, func.count(Lead.id))
            .where(Lead.campaign_id == campaign_id)
            .group_by(Lead.status)
        ).all()
    )
    calls_total = s.scalar(select(func.count(Call.id)).where(Call.campaign_id == campaign_id)) or 0
    minutes = s.scalar(
        select(func.coalesce(func.sum(Call.duration_seconds), 0)).where(
            Call.campaign_id == campaign_id
        )
    ) or 0
    cost = s.scalar(
        select(func.coalesce(func.sum(Call.estimated_cost_usd), 0.0)).where(
            Call.campaign_id == campaign_id
        )
    ) or 0.0
    return {
        "leads_by_status": by_status,
        "leads_total": sum(by_status.values()),
        "calls_total": calls_total,
        "talk_minutes": round(minutes / 60.0, 1),
        "estimated_cost_usd": round(float(cost), 2),
    }


@router.get("/campaigns/{campaign_id}", dependencies=dep)
def get_campaign(campaign_id: str) -> dict[str, Any]:
    with session_scope() as s:
        c = s.get(Campaign, campaign_id)
        if not c:
            raise HTTPException(404, "campaign not found")
        d = c.as_dict()
        d["stats"] = _campaign_stats(s, c.id)
        return d


@router.post("/campaigns/{campaign_id}/start", dependencies=dep)
def start_campaign(campaign_id: str) -> dict[str, Any]:
    with session_scope() as s:
        c = s.get(Campaign, campaign_id)
        if not c:
            raise HTTPException(404, "campaign not found")
        c.status = "running"
        return c.as_dict()


@router.post("/campaigns/{campaign_id}/pause", dependencies=dep)
def pause_campaign(campaign_id: str) -> dict[str, Any]:
    with session_scope() as s:
        c = s.get(Campaign, campaign_id)
        if not c:
            raise HTTPException(404, "campaign not found")
        c.status = "paused"
        return c.as_dict()


@router.post("/campaigns/{campaign_id}/leads", dependencies=dep)
def attach_leads(campaign_id: str, lead_ids: list[str]) -> dict[str, Any]:
    with session_scope() as s:
        if not s.get(Campaign, campaign_id):
            raise HTTPException(404, "campaign not found")
        n = 0
        for lid in lead_ids:
            lead = s.get(Lead, lid)
            if lead:
                lead.campaign_id = campaign_id
                if lead.status == "new":
                    lead.status = "queued"
                n += 1
    return {"attached": n}


# ----------------------------------------------------------------------
# calls
# ----------------------------------------------------------------------

class OutboundCallIn(BaseModel):
    # Either an existing lead...
    lead_id: str | None = None
    # ...or an ad-hoc number.
    phone: str | None = None
    agent_id: str | None = None
    goal: str | None = None


@router.post("/calls", dependencies=dep)
def place_call(body: OutboundCallIn) -> dict[str, Any]:
    """Trigger a single outbound call right now (no campaign needed)."""
    from outreach.campaigns import dial_lead

    if body.lead_id:
        try:
            call_id = dial_lead(body.lead_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"call_id": call_id}

    if not body.phone:
        raise HTTPException(400, "provide lead_id or phone")
    # Ad-hoc: create a lead row so the call has somewhere to record outcome.
    with session_scope() as s:
        lead = Lead(phone=body.phone, status="queued")
        s.add(lead)
        s.flush()
        lead_id = lead.id
    try:
        call_id = dial_lead(lead_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"call_id": call_id, "lead_id": lead_id}


@router.get("/calls", dependencies=dep)
def list_calls(
    status: str | None = None,
    campaign_id: str | None = None,
    agent_id: str | None = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
) -> list[dict[str, Any]]:
    with session_scope() as s:
        stmt = select(Call).order_by(Call.created_at.desc()).limit(limit).offset(offset)
        if status:
            stmt = stmt.where(Call.status == status)
        if campaign_id:
            stmt = stmt.where(Call.campaign_id == campaign_id)
        if agent_id:
            stmt = stmt.where(Call.agent_id == agent_id)
        return [c.as_dict() for c in s.scalars(stmt)]


@router.get("/calls/{call_id}", dependencies=dep)
def get_call(call_id: str) -> dict[str, Any]:
    with session_scope() as s:
        c = s.get(Call, call_id)
        if not c:
            raise HTTPException(404, "call not found")
        return c.as_dict(include_transcript=True)


@router.post("/calls/{call_id}/transfer", dependencies=dep)
def transfer_call(call_id: str, to_number: str = Query(...)) -> dict[str, str]:
    """Cold-transfer a live call to a human."""
    from outreach.telephony import TelephonyError, get_carrier

    with session_scope() as s:
        c = s.get(Call, call_id)
        if not c or not c.provider_call_id:
            raise HTTPException(404, "call not found or not yet connected")
        provider_id = c.provider_call_id
    try:
        get_carrier().transfer(provider_id, to_number)
    except TelephonyError as exc:
        raise HTTPException(502, str(exc))
    return {"status": "transferring", "to": to_number}


# ----------------------------------------------------------------------
# DNC
# ----------------------------------------------------------------------

@router.get("/dnc", dependencies=dep)
def list_dnc() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [
            {"phone": e.phone, "reason": e.reason, "created_at": e.created_at.isoformat()}
            for e in s.scalars(select(DNCEntry))
        ]


@router.post("/dnc", dependencies=dep)
def add_dnc(phone: str = Query(...), reason: str | None = None) -> dict[str, str]:
    with session_scope() as s:
        if not s.get(DNCEntry, phone):
            s.add(DNCEntry(phone=phone, reason=reason))
    return {"added": phone}


@router.delete("/dnc/{phone}", dependencies=dep)
def remove_dnc(phone: str) -> dict[str, str]:
    with session_scope() as s:
        e = s.get(DNCEntry, phone)
        if e:
            s.delete(e)
    return {"removed": phone}


# ----------------------------------------------------------------------
# stats (dashboard overview)
# ----------------------------------------------------------------------

@router.get("/stats", dependencies=dep)
def stats() -> dict[str, Any]:
    with session_scope() as s:
        calls_by_status = dict(
            s.execute(select(Call.status, func.count(Call.id)).group_by(Call.status)).all()
        )
        leads_by_status = dict(
            s.execute(select(Lead.status, func.count(Lead.id)).group_by(Lead.status)).all()
        )
        minutes = s.scalar(select(func.coalesce(func.sum(Call.duration_seconds), 0))) or 0
        cost = s.scalar(select(func.coalesce(func.sum(Call.estimated_cost_usd), 0.0))) or 0.0
        campaigns_running = s.scalar(
            select(func.count(Campaign.id)).where(Campaign.status == "running")
        ) or 0
    return {
        "calls_by_status": calls_by_status,
        "calls_total": sum(calls_by_status.values()),
        "leads_by_status": leads_by_status,
        "leads_total": sum(leads_by_status.values()),
        "talk_minutes": round(minutes / 60.0, 1),
        "estimated_cost_usd": round(float(cost), 2),
        "campaigns_running": campaigns_running,
    }
