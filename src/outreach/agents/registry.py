"""Discover agent configs on disk and resolve a dialed number to an agent.

The registry scans the top-level ``agents/`` directory (one folder per
client, each containing ``config.yaml``). This is the single seam that makes
the platform multi-tenant: routing an inbound call is just a dict lookup
from the dialed number to a loaded :class:`AgentConfig`.
"""

from __future__ import annotations

from pathlib import Path

from outreach.actions.registry import known_action_names
from outreach.agents.schema import AgentConfig, load_agent_config

# Repo-root/agents — three parents up from this file: agents/ <- outreach/ <- src/.
_DEFAULT_AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"


class AgentRegistry:
    def __init__(self, agents_dir: Path | None = None) -> None:
        self.agents_dir = agents_dir or _DEFAULT_AGENTS_DIR
        self._by_id: dict[str, AgentConfig] = {}
        self._by_number: dict[str, AgentConfig] = {}

    def load(self) -> "AgentRegistry":
        """Load and validate every agent config. Fails loudly on bad data so
        a broken config is caught at startup, not mid-call."""
        self._by_id.clear()
        self._by_number.clear()

        if not self.agents_dir.is_dir():
            raise FileNotFoundError(f"agents directory not found: {self.agents_dir}")

        valid_actions = set(known_action_names())

        for config_file in sorted(self.agents_dir.glob("*/config.yaml")):
            agent = load_agent_config(config_file)

            # Folder name is the source of truth for the id.
            folder_id = config_file.parent.name
            if agent.agent_id != folder_id:
                raise ValueError(
                    f"agent_id '{agent.agent_id}' in {config_file} does not match "
                    f"its folder '{folder_id}'."
                )

            unknown = [a for a in agent.allowed_actions if a not in valid_actions]
            if unknown:
                raise ValueError(
                    f"agent '{agent.agent_id}' lists unknown action(s) {unknown}. "
                    f"Known actions: {sorted(valid_actions)}."
                )

            if agent.agent_id in self._by_id:
                raise ValueError(f"duplicate agent_id '{agent.agent_id}'.")
            self._by_id[agent.agent_id] = agent

            if agent.phone_number:
                if agent.phone_number in self._by_number:
                    other = self._by_number[agent.phone_number].agent_id
                    raise ValueError(
                        f"phone_number {agent.phone_number} is claimed by both "
                        f"'{agent.agent_id}' and '{other}'."
                    )
                self._by_number[agent.phone_number] = agent

        if not self._by_id:
            raise ValueError(f"no agent configs found under {self.agents_dir}")
        return self

    # --- lookups ---

    def by_id(self, agent_id: str) -> AgentConfig:
        return self._by_id[agent_id]

    def for_number(self, dialed_number: str, default_agent_id: str | None = None) -> AgentConfig:
        """Resolve the agent that should answer ``dialed_number``.

        Falls back to ``default_agent_id`` when the number isn't mapped — this
        is what lets a shared Twilio trial number reach the test agent before
        the real (e.g. Indian) DID is provisioned.
        """
        agent = self._by_number.get((dialed_number or "").strip())
        if agent is not None:
            return agent
        if default_agent_id and default_agent_id in self._by_id:
            return self._by_id[default_agent_id]
        raise KeyError(
            f"no agent mapped to {dialed_number!r} and no valid default agent."
        )

    @property
    def agent_ids(self) -> list[str]:
        return sorted(self._by_id)


def get_registry(agents_dir: Path | None = None) -> AgentRegistry:
    """Build and load a registry. Kept as a function (not a singleton) so it's
    trivial to point at a fixture directory in tests."""
    return AgentRegistry(agents_dir).load()
