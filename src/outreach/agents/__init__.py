"""Per-client agent configuration: schema + registry.

An "agent" is one client's configured persona, knowledge, and permitted
actions, identified by the phone number callers dial. Configs are plain
files under the top-level ``agents/`` directory so a non-developer can add
or tweak a client without touching Python.
"""

from outreach.agents.schema import (
    AgentConfig,
    EscalationConfig,
    PersonaConfig,
    ProviderOverrides,
)
from outreach.agents.registry import AgentRegistry, get_registry

__all__ = [
    "AgentConfig",
    "PersonaConfig",
    "ProviderOverrides",
    "EscalationConfig",
    "AgentRegistry",
    "get_registry",
]
