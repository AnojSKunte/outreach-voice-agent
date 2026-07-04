"""Twilio implementation of the carrier interface.

Outbound flow:
1. ``originate_call`` hits Twilio REST with inline TwiML that opens a Media
   Streams WebSocket back to our server, embedding ``call_id`` / ``agent_id``
   / ``lead_id`` as custom <Parameter>s so the WS handler knows exactly which
   call it is serving.
2. Twilio async AMD (answering machine detection) posts its verdict to
   ``/telephony/amd`` — the server decides to continue, leave a message, or
   hang up.
3. Call status changes (ringing/answered/completed/no-answer/busy/failed)
   post to ``/telephony/status`` and update the Call row + fire webhooks.
"""

from __future__ import annotations

from html import escape

from loguru import logger

from outreach.config import Settings
from outreach.telephony.base import Carrier, TelephonyError


class TwilioCarrier(Carrier):
    name = "twilio"

    def __init__(self, settings: Settings) -> None:
        settings.require("twilio_account_sid", "twilio_auth_token", "twilio_from_number")
        if not settings.public_host:
            raise TelephonyError(
                "PUBLIC_HOST must be set (e.g. your-app.onrender.com) so Twilio "
                "can reach the media WebSocket."
            )
        self.settings = settings
        from twilio.rest import Client

        self.client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    # ------------------------------------------------------------------

    def stream_twiml(
        self,
        *,
        call_id: str,
        agent_id: str,
        direction: str,
        lead_id: str | None = None,
        campaign_id: str | None = None,
    ) -> str:
        """TwiML that connects the call's audio to our WebSocket."""
        host = self.settings.public_host
        params = {
            "call_id": call_id,
            "agent_id": agent_id,
            "direction": direction,
        }
        if lead_id:
            params["lead_id"] = lead_id
        if campaign_id:
            params["campaign_id"] = campaign_id
        param_xml = "".join(
            f'<Parameter name="{escape(k)}" value="{escape(str(v))}"/>' for k, v in params.items()
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Connect>"
            f'<Stream url="wss://{host}/ws">{param_xml}</Stream>'
            "</Connect></Response>"
        )

    def originate_call(
        self,
        to_number: str,
        *,
        agent_id: str,
        call_id: str,
        lead_id: str | None = None,
        campaign_id: str | None = None,
        detect_voicemail: bool = True,
    ) -> str:
        host = self.settings.public_host
        twiml = self.stream_twiml(
            call_id=call_id,
            agent_id=agent_id,
            direction="outbound",
            lead_id=lead_id,
            campaign_id=campaign_id,
        )
        kwargs: dict = {
            "to": to_number,
            "from_": self.settings.twilio_from_number,
            "twiml": twiml,
            "status_callback": f"https://{host}/telephony/status?call_id={call_id}",
            "status_callback_event": ["initiated", "ringing", "answered", "completed"],
            "status_callback_method": "POST",
        }
        if detect_voicemail:
            # Async AMD: the bot connects immediately; the verdict arrives on
            # a callback so a human hears no dead air.
            kwargs.update(
                machine_detection="Enable",
                async_amd="true",
                async_amd_status_callback=f"https://{host}/telephony/amd?call_id={call_id}",
                async_amd_status_callback_method="POST",
            )
        try:
            call = self.client.calls.create(**kwargs)
        except Exception as exc:  # twilio.base.exceptions.TwilioRestException etc.
            raise TelephonyError(f"Twilio dial-out to {to_number} failed: {exc}") from exc
        logger.info(
            f"originated call {call_id} -> {to_number} (twilio sid {call.sid}); "
            f"media stream target wss://{host}/ws"
        )
        return call.sid

    def hangup(self, provider_call_id: str) -> None:
        try:
            self.client.calls(provider_call_id).update(status="completed")
        except Exception as exc:
            raise TelephonyError(f"hangup failed for {provider_call_id}: {exc}") from exc

    def transfer(self, provider_call_id: str, to_number: str) -> None:
        """Cold transfer: re-point the live call at a <Dial> to the human.

        The media stream leg ends and Twilio bridges the caller to the number.
        """
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Dial>{escape(to_number)}</Dial></Response>"
        )
        try:
            self.client.calls(provider_call_id).update(twiml=twiml)
        except Exception as exc:
            raise TelephonyError(f"transfer failed for {provider_call_id}: {exc}") from exc
