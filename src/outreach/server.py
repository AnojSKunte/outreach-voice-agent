"""The web service: telephony webhooks, media WebSocket, API, dashboard.

One process runs everything:
* ``POST /twiml``          — Twilio inbound-call webhook (returns Stream TwiML)
* ``WS   /ws``             — carrier media stream; runs the voice pipeline
* ``POST /telephony/status`` — carrier call-status callbacks
* ``POST /telephony/amd``  — Twilio async answering-machine-detection verdicts
* ``/api/v1/...``          — REST API (see ``outreach.api``)
* ``GET /``                — dashboard
* ``GET /health``          — deploy health check

The campaign engine runs as an asyncio task inside this process, so a single
deployment (Render/EC2/anywhere) is the whole product.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse
from loguru import logger

from outreach import calls as call_service
from outreach.agents import get_registry
from outreach.api import router as api_router
from outreach.config import get_settings
from outreach.db.session import init_db

_DASHBOARD = Path(__file__).resolve().parent / "dashboard" / "index.html"

_registry = None
_stop_event: asyncio.Event | None = None


def registry():
    global _registry
    if _registry is None:
        _registry = get_registry()
    return _registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stop_event
    init_db()
    registry()  # fail fast on bad agent configs
    from outreach import events
    from outreach.campaigns import run_campaign_loop

    events.set_main_loop(asyncio.get_running_loop())

    _stop_event = asyncio.Event()
    engine_task = asyncio.create_task(run_campaign_loop(_stop_event))
    logger.info("outreach server up")
    yield
    _stop_event.set()
    await engine_task


app = FastAPI(title="Outreach — AI Calling Agent", lifespan=lifespan)
app.include_router(api_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agents": registry().agent_ids}


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(_DASHBOARD, media_type="text/html")


# ----------------------------------------------------------------------
# inbound calls (Twilio webhook -> TwiML pointing at /ws)
# ----------------------------------------------------------------------

@app.post("/twiml")
async def inbound_twiml(request: Request) -> PlainTextResponse:
    """Twilio hits this when someone calls one of our numbers. We resolve the
    agent from the dialed number, create the call record, and return TwiML
    that opens the media stream with our ids embedded."""
    form = await request.form()
    to_number = str(form.get("To", ""))
    from_number = str(form.get("From", ""))
    provider_call_id = str(form.get("CallSid", ""))

    settings = get_settings()
    try:
        agent = registry().for_number(to_number, settings.default_agent_id)
    except KeyError:
        logger.warning(f"inbound call to unmapped number {to_number}; rejecting")
        return PlainTextResponse(
            '<?xml version="1.0" encoding="UTF-8"?><Response><Reject/></Response>',
            media_type="application/xml",
        )

    call_id = call_service.create_call(
        direction="inbound",
        agent_id=agent.agent_id,
        to_number=to_number,
        from_number=from_number,
        provider_call_id=provider_call_id,
        status="ringing",
    )

    from outreach.telephony.twilio_carrier import TwilioCarrier

    twiml = TwilioCarrier(settings).stream_twiml(
        call_id=call_id, agent_id=agent.agent_id, direction="inbound"
    )
    return PlainTextResponse(twiml, media_type="application/xml")


# ----------------------------------------------------------------------
# media WebSocket — the live call
# ----------------------------------------------------------------------

@app.websocket("/ws")
async def media_ws(websocket: WebSocket) -> None:
    await websocket.accept()

    # Heavy imports deferred to call time so the API/dashboard work without
    # voice deps installed (and boot stays fast).
    from pipecat.runner.utils import parse_telephony_websocket
    from pipecat.serializers.twilio import TwilioFrameSerializer

    try:  # Pipecat 1.x current path
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
    except ImportError:  # older layout
        from pipecat.transports.network.fastapi_websocket import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    from outreach.pipeline.builder import build_voice_session

    settings = get_settings()

    transport_type, call_data = await parse_telephony_websocket(websocket)
    custom = dict(call_data.get("body") or {})
    call_id = custom.get("call_id")
    agent_id = custom.get("agent_id") or settings.default_agent_id
    direction = custom.get("direction", "inbound")
    lead_id = custom.get("lead_id")

    logger.info(
        f"media stream open: transport={transport_type} call={call_id} "
        f"agent={agent_id} direction={direction}"
    )

    try:
        agent = registry().by_id(agent_id)
    except KeyError:
        logger.error(f"unknown agent '{agent_id}' on media stream; closing")
        await websocket.close()
        return

    # Carrier-specific frame serializer (Twilio default; Exotel supported).
    if str(transport_type).lower().find("exotel") >= 0:
        from pipecat.serializers.exotel import ExotelFrameSerializer

        serializer = ExotelFrameSerializer(stream_id=call_data["stream_id"])
    else:
        serializer = TwilioFrameSerializer(
            stream_sid=call_data["stream_id"],
            call_sid=call_data.get("call_id"),
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
        )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    # Per-call context (outbound: lead facts + campaign goal).
    call_context: dict = {"direction": direction}
    if lead_id:
        from outreach.db.models import Campaign, Lead
        from outreach.db.session import session_scope

        with session_scope() as s:
            lead = s.get(Lead, lead_id)
            if lead:
                call_context["lead"] = {
                    "name": lead.name,
                    "company": lead.company,
                    "notes": lead.notes,
                    **(lead.custom or {}),
                }
                if lead.campaign_id:
                    camp = s.get(Campaign, lead.campaign_id)
                    if camp and camp.goal:
                        call_context["goal"] = camp.goal

    session = build_voice_session(
        agent, transport, settings=settings, call_context=call_context
    )

    if call_id:
        call_service.mark_started(call_id, profile=session.profile)

    status = "completed"
    try:
        await session.run()
    except Exception as exc:
        logger.exception("voice session crashed")
        status = "failed"
    finally:
        if call_id:
            # Persist transcript + duration, run analysis, update lead, webhooks.
            transcript = session.snapshot_transcript()
            await asyncio.to_thread(call_service.finalize_call, call_id, transcript, status)


# ----------------------------------------------------------------------
# carrier callbacks
# ----------------------------------------------------------------------

_TWILIO_STATUS_MAP = {
    "initiated": "initiated",
    "ringing": "ringing",
    "in-progress": "in_progress",
    "answered": "in_progress",
    "completed": "completed",
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "canceled",
}


@app.post("/telephony/status")
async def telephony_status(request: Request, call_id: str = "") -> dict:
    form = await request.form()
    raw = str(form.get("CallStatus", "")).lower()
    status = _TWILIO_STATUS_MAP.get(raw)
    logger.info(f"status callback call={call_id} twilio={raw} -> {status}")
    if not call_id or not status:
        return {"ok": True}

    if status in ("busy", "no_answer", "failed", "canceled"):
        await asyncio.to_thread(call_service.handle_missed_outbound, call_id, status)
    elif status in ("ringing", "initiated"):
        await asyncio.to_thread(call_service.update_status, call_id, status)
    # 'completed' is handled by the WS finalizer (it owns the transcript).
    return {"ok": True}


@app.post("/telephony/amd")
async def telephony_amd(request: Request, call_id: str = "") -> dict:
    """Twilio async AMD verdict. If a machine answered, apply the agent's
    voicemail policy: hang up, or say the voicemail message then hang up."""
    form = await request.form()
    answered_by = str(form.get("AnsweredBy", "")).lower()
    provider_call_id = str(form.get("CallSid", ""))
    logger.info(f"AMD verdict call={call_id}: {answered_by}")

    if not answered_by.startswith("machine"):
        return {"ok": True}  # human / unknown — let the conversation continue

    # Look up the agent's voicemail policy.
    settings = get_settings()
    agent = None
    if call_id:
        from outreach.db.models import Call
        from outreach.db.session import session_scope

        with session_scope() as s:
            call = s.get(Call, call_id)
            if call:
                try:
                    agent = registry().by_id(call.agent_id)
                except KeyError:
                    agent = None

    action = agent.outbound.voicemail_action if agent else "hangup"
    message = agent.outbound.voicemail_message if agent else None

    try:
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        if action == "leave_message" and message:
            # Wait for the beep pass-through isn't available on async AMD with
            # 'Enable'; a short pause before speaking is the pragmatic fix.
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?><Response>'
                f'<Pause length="2"/><Say>{message}</Say><Hangup/></Response>'
            )
            client.calls(provider_call_id).update(twiml=twiml)
        else:
            client.calls(provider_call_id).update(status="completed")
    except Exception as exc:
        logger.warning(f"AMD handling failed for {provider_call_id}: {exc}")

    if call_id:
        await asyncio.to_thread(call_service.handle_missed_outbound, call_id, "voicemail")
    return {"ok": True}


# ----------------------------------------------------------------------
# TEMPORARY remote diagnostics (keyed). Lets an operator read recent logs
# and trigger a test dial without shell access. Remove before selling.
# ----------------------------------------------------------------------

from collections import deque as _deque

_LOG_BUFFER: "_deque[str]" = _deque(maxlen=500)
logger.add(lambda m: _LOG_BUFFER.append(str(m)), level="INFO")


def _debug_auth(key: str) -> None:
    from fastapi import HTTPException

    expected = get_settings().outreach_api_key
    if expected and key != expected:
        raise HTTPException(401, "bad key")


@app.get("/debug/{key}/logs/{nonce}")
def debug_logs(key: str, nonce: str) -> PlainTextResponse:
    _debug_auth(key)
    lines = list(_LOG_BUFFER)[-250:]
    return PlainTextResponse("".join(lines) or "(log buffer empty)")


@app.get("/debug/{key}/calls/{nonce}")
def debug_calls(key: str, nonce: str) -> dict:
    _debug_auth(key)
    from sqlalchemy import select

    from outreach.db.models import Call
    from outreach.db.session import session_scope

    with session_scope() as s:
        rows = list(
            s.scalars(select(Call).order_by(Call.created_at.desc()).limit(8))
        )
        return {"calls": [c.as_dict(include_transcript=True) for c in rows]}


@app.get("/debug/{key}/dial/{phone_digits}/{nonce}")
async def debug_dial(key: str, phone_digits: str, nonce: str) -> dict:
    """Dial +<phone_digits> (digits only in the path; '+' is implied)."""
    _debug_auth(key)
    phone = "+" + "".join(ch for ch in phone_digits if ch.isdigit())
    from outreach.db.models import Lead
    from outreach.db.session import session_scope
    from outreach.campaigns import dial_lead

    with session_scope() as s:
        lead = Lead(phone=phone, name="Debug Test", status="queued")
        s.add(lead)
        s.flush()
        lead_id = lead.id
    call_id = await asyncio.to_thread(dial_lead, lead_id)
    return {"call_id": call_id, "lead_id": lead_id, "phone": phone}
