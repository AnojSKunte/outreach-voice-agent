"""Tests for the action -> tool-spec mapping that feeds the LLM.

These stay dependency-light (no Pipecat needed): they validate the
provider-neutral tool descriptors built from the action registry, which is
what both the live pipeline and scripts/try_agent.py rely on.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from outreach.actions import get_action  # noqa: E402
from outreach.agents import get_registry  # noqa: E402


def test_tool_spec_shape_is_openai_compatible():
    spec = get_action("check_availability").to_tool_spec()
    assert spec["type"] == "function"
    fn = spec["function"]
    assert fn["name"] == "check_availability"
    assert fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    # Required args are declared and present in properties.
    for req in params["required"]:
        assert req in params["properties"]


def test_every_allowed_action_has_a_valid_spec():
    agent = get_registry().by_id("seaside-hotel")
    assert agent.allowed_actions  # sanity: the sample agent can do something
    for name in agent.allowed_actions:
        spec = get_action(name).to_tool_spec()
        assert spec["function"]["name"] == name
        assert isinstance(spec["function"]["parameters"]["properties"], dict)
