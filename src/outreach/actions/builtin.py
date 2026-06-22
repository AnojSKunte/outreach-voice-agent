"""Built-in actions for phase 1.

These are deliberately mock-backed: they prove the agent can *decide* to act
and the pipeline can execute a tool and feed the result back into the
conversation. Swapping a handler body for a real PMS/booking API call later
does not change the agent contract or the pipeline.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from outreach.actions.registry import register

# A trivial pretend inventory so availability answers are deterministic and
# demoable. Real implementation would query the client's booking system.
_ROOM_TYPES = {
    "standard": {"nightly_rate_usd": 120, "max_guests": 2},
    "deluxe": {"nightly_rate_usd": 180, "max_guests": 3},
    "suite": {"nightly_rate_usd": 320, "max_guests": 4},
}


@register(
    name="check_availability",
    description=(
        "Check room availability and nightly price for a stay. Use when the "
        "caller asks whether rooms are free, or about prices for given dates."
    ),
    parameters={
        "type": "object",
        "properties": {
            "check_in": {
                "type": "string",
                "description": "Check-in date, ISO format YYYY-MM-DD.",
            },
            "nights": {
                "type": "integer",
                "minimum": 1,
                "description": "Number of nights.",
            },
            "guests": {
                "type": "integer",
                "minimum": 1,
                "description": "Number of guests.",
            },
            "room_type": {
                "type": "string",
                "enum": list(_ROOM_TYPES.keys()),
                "description": "Optional preferred room type.",
            },
        },
        "required": ["check_in", "nights", "guests"],
    },
)
def check_availability(args: dict[str, Any]) -> dict[str, Any]:
    try:
        check_in = date.fromisoformat(args["check_in"])
    except (KeyError, ValueError):
        return {"error": "I need a valid check-in date (year, month and day)."}

    nights = int(args.get("nights", 1))
    guests = int(args.get("guests", 1))
    preferred = args.get("room_type")

    if check_in < datetime.now().date():
        return {"available": False, "reason": "That date is in the past."}

    # Mock rule: a room type is available if it fits the party size.
    candidates = {
        name: spec
        for name, spec in _ROOM_TYPES.items()
        if spec["max_guests"] >= guests and (preferred is None or name == preferred)
    }
    if not candidates:
        return {
            "available": False,
            "reason": f"No room fits {guests} guests for the requested type.",
        }

    options = [
        {
            "room_type": name,
            "nightly_rate_usd": spec["nightly_rate_usd"],
            "total_usd": spec["nightly_rate_usd"] * nights,
        }
        for name, spec in candidates.items()
    ]
    return {
        "available": True,
        "check_in": check_in.isoformat(),
        "nights": nights,
        "guests": guests,
        "options": options,
    }


@register(
    name="escalate_to_human",
    description=(
        "Hand the call off to a human team member. Use when the caller asks "
        "for a person, is upset, or needs something outside your knowledge."
    ),
    parameters={
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Short reason for the handoff, for the human's context.",
            },
        },
        "required": ["reason"],
    },
)
def escalate_to_human(args: dict[str, Any]) -> dict[str, Any]:
    # Phase 1: signal intent only. The pipeline/telephony layer interprets this
    # (e.g. play the escalation message and transfer) in a later milestone.
    return {
        "status": "escalation_requested",
        "reason": args.get("reason", "unspecified"),
    }
