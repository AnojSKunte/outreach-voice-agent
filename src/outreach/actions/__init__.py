"""Actions = the tools an agent may invoke during a call.

Each action is registered once with a name, a description, a JSON-schema
parameter spec (what the LLM must supply), and a handler. Agents opt into
actions by name in their config, so what an agent is *allowed* to do is data,
not code. This is the seam where real integrations (PMS, CRM, calendar) get
plugged in later — for phase 1 the handlers are mock implementations.
"""

from outreach.actions.registry import (
    Action,
    get_action,
    known_action_names,
    register,
)
import outreach.actions.builtin  # noqa: F401  (registers built-in actions on import)

__all__ = ["Action", "register", "get_action", "known_action_names"]
