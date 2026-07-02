"""Telephony carriers: originate outbound calls, transfer, hang up.

Twilio is the default (dev + international). Exotel is the India production
path — same interface, so the campaign engine and API don't care which
carrier is active. Selected via TELEPHONY_PROVIDER.
"""

from outreach.telephony.base import TelephonyError, get_carrier

__all__ = ["get_carrier", "TelephonyError"]
