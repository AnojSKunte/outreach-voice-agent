"""Exotel implementation — the India production carrier.

Why Exotel: Twilio cannot issue Indian local numbers and international
caller IDs tank pickup rates (and TRAI treats unsolicited international
commercial calls as UCC). Exotel provides Indian DIDs, DLT-compliant
domestic routes, and its AgentStream/Voicebot applet speaks a bidirectional
WebSocket protocol that Pipecat supports natively via
``pipecat.serializers.exotel.ExotelFrameSerializer``.

Setup (one-time, in the Exotel dashboard):
1. Buy an ExoPhone (Indian number).
2. Create a "Voicebot" applet pointing at ``wss://<PUBLIC_HOST>/ws``.
3. Attach the applet to the ExoPhone's call flow (inbound), and note the
   flow/app id for outbound connects.
4. Set EXOTEL_* env vars.

Outbound uses Exotel's Connect API: it first dials the lead from your
ExoPhone, then bridges the answered call into the Voicebot applet (our WS).
Custom parameters ride along as ``CustomField`` and come back in the WS
start message.
"""

from __future__ import annotations

import json

import requests as _requests  # sync; campaign engine calls via thread executor
from loguru import logger

from outreach.config import Settings
from outreach.telephony.base import Carrier, TelephonyError


class ExotelCarrier(Carrier):
    name = "exotel"

    def __init__(self, settings: Settings) -> None:
        settings.require("exotel_sid", "exotel_api_key", "exotel_api_token", "exotel_from_number")
        self.settings = settings
        self.base = (
            f"https://{settings.exotel_api_key}:{settings.exotel_api_token}"
            f"@{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}"
        )

    def originate_call(
        self,
        to_number: str,
        *,
        agent_id: str,
        call_id: str,
        lead_id: str | None = None,
        campaign_id: str | None = None,
        detect_voicemail: bool = True,  # Exotel AMD availability varies by plan
    ) -> str:
        custom = json.dumps(
            {
                "call_id": call_id,
                "agent_id": agent_id,
                "direction": "outbound",
                "lead_id": lead_id,
                "campaign_id": campaign_id,
            }
        )
        # Connect the lead to the Voicebot applet flow. The applet must be
        # configured with our wss:// URL (see module docstring).
        payload = {
            "From": to_number,
            "CallerId": self.settings.exotel_from_number,
            "Url": f"http://my.exotel.com/{self.settings.exotel_sid}/exoml/start_voice/voicebot",
            "CustomField": custom,
        }
        try:
            resp = _requests.post(f"{self.base}/Calls/connect.json", data=payload, timeout=15)
            resp.raise_for_status()
            sid = resp.json()["Call"]["Sid"]
        except Exception as exc:
            raise TelephonyError(f"Exotel dial-out to {to_number} failed: {exc}") from exc
        logger.info(f"originated call {call_id} -> {to_number} (exotel sid {sid})")
        return sid

    def hangup(self, provider_call_id: str) -> None:
        # Exotel ends the call when the WS closes; explicit hangup endpoint
        # is not uniformly available. Closing our end is the reliable path.
        logger.info(f"exotel hangup requested for {provider_call_id} (close WS to end)")

    def transfer(self, provider_call_id: str, to_number: str) -> None:
        raise TelephonyError(
            "Live transfer on Exotel requires a dial-to-agent leg in the "
            "Exotel flow builder. Configure a 'Connect' applet after the "
            "Voicebot in your flow, or use the callback pattern instead."
        )
