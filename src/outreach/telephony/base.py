"""Carrier-neutral telephony interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from outreach.config import Settings, get_settings


class TelephonyError(RuntimeError):
    pass


class Carrier(ABC):
    """What the platform needs from a phone carrier."""

    name: str = "base"

    @abstractmethod
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
        """Start an outbound call; return the carrier's call id."""

    @abstractmethod
    def hangup(self, provider_call_id: str) -> None:
        """End a live call."""

    @abstractmethod
    def transfer(self, provider_call_id: str, to_number: str) -> None:
        """Cold-transfer a live call to a human number."""


def get_carrier(settings: Settings | None = None) -> Carrier:
    settings = settings or get_settings()
    provider = settings.telephony_provider.lower()
    if provider == "twilio":
        from outreach.telephony.twilio_carrier import TwilioCarrier

        return TwilioCarrier(settings)
    if provider == "exotel":
        from outreach.telephony.exotel_carrier import ExotelCarrier

        return ExotelCarrier(settings)
    raise TelephonyError(
        f"Unknown TELEPHONY_PROVIDER '{provider}'. Use 'twilio' or 'exotel'."
    )
