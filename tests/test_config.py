"""Foundation tests — no API keys, no network. Prove the multi-tenant
config/registry/actions wiring is sound before any provider is involved."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from outreach.actions import get_action, known_action_names  # noqa: E402
from outreach.agents import AgentConfig, get_registry  # noqa: E402
from outreach.agents.schema import PersonaConfig  # noqa: E402


def test_builtin_actions_registered():
    names = known_action_names()
    assert "check_availability" in names
    assert "escalate_to_human" in names


def test_seaside_hotel_loads_and_routes():
    reg = get_registry()
    assert "seaside-hotel" in reg.agent_ids

    agent = reg.by_id("seaside-hotel")
    assert agent.client_name == "Seaside Hotel"
    assert "check_availability" in agent.allowed_actions
    # Knowledge file was inlined.
    assert "Seaside Hotel" in agent.knowledge_base
    # System prompt includes persona + knowledge.
    prompt = agent.system_prompt()
    assert "Mia" in prompt and "Check-in" in prompt


def test_unmapped_number_falls_back_to_default():
    reg = get_registry()
    # seaside-hotel has no phone_number, so an arbitrary dialed number must
    # resolve via the default agent id.
    agent = reg.for_number("+10000000000", default_agent_id="seaside-hotel")
    assert agent.agent_id == "seaside-hotel"

    with pytest.raises(KeyError):
        reg.for_number("+10000000000", default_agent_id=None)


def test_phone_number_must_be_e164():
    with pytest.raises(ValueError):
        AgentConfig(
            agent_id="x",
            client_name="X",
            phone_number="555 1234",  # no '+'
            persona=PersonaConfig(name="A", role="b"),
        )


def test_check_availability_action_runs():
    action = get_action("check_availability")
    result = action.handler({"check_in": "2099-01-01", "nights": 2, "guests": 2})
    assert result["available"] is True
    assert result["nights"] == 2
    assert any(o["room_type"] == "standard" for o in result["options"])

    # Party too large for any room type.
    none_fit = action.handler({"check_in": "2099-01-01", "nights": 1, "guests": 9})
    assert none_fit["available"] is False
