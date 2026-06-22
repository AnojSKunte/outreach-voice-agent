"""Load and validate every agent config, then print a summary.

Run this after editing any agent file — it catches bad configs, unknown
actions, and number clashes before they ever reach a live call.

    python scripts/validate_agents.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `src/` importable when run directly from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from outreach.actions import known_action_names  # noqa: E402
from outreach.agents import get_registry  # noqa: E402


def main() -> int:
    try:
        registry = get_registry()
    except Exception as exc:  # noqa: BLE001 — surface the problem plainly
        print(f"✗ agent config validation failed: {exc}")
        return 1

    print(f"✓ loaded {len(registry.agent_ids)} agent(s)")
    print(f"  known actions: {', '.join(known_action_names())}\n")

    for agent_id in registry.agent_ids:
        a = registry.by_id(agent_id)
        number = a.phone_number or "(unmapped — uses default routing)"
        print(f"  • {a.agent_id}  [{a.client_name}]")
        print(f"      number : {number}")
        print(f"      persona: {a.persona.name} — {a.persona.role}")
        print(f"      actions: {', '.join(a.allowed_actions) or '(none)'}")
        print(f"      kb     : {len(a.knowledge_base)} chars")
        print(f"      prompt : {len(a.system_prompt())} chars rendered")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
