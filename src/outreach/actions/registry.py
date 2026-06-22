"""A tiny, provider-neutral action registry.

Actions are described in a shape that maps cleanly onto OpenAI-style
function/tool calling, but nothing here imports an LLM SDK — the pipeline
layer adapts these descriptors to whatever provider is in use. That keeps
the "what can the agent do" contract independent of "which LLM".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

# A handler takes the validated arguments dict and returns a JSON-serialisable
# result the LLM will see. May be sync or async.
Handler = Callable[[dict[str, Any]], "Awaitable[Any] | Any"]


@dataclass(frozen=True)
class Action:
    name: str
    description: str
    # JSON Schema for the parameters object (properties/required/...).
    parameters: dict[str, Any]
    handler: Handler

    def to_tool_spec(self) -> dict[str, Any]:
        """Render as an OpenAI-style tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, Action] = {}


def register(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> Callable[[Handler], Handler]:
    """Decorator: register a handler as a named action."""

    def _decorator(handler: Handler) -> Handler:
        if name in _REGISTRY:
            raise ValueError(f"action '{name}' already registered.")
        _REGISTRY[name] = Action(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )
        return handler

    return _decorator


def get_action(name: str) -> Action:
    return _REGISTRY[name]


def known_action_names() -> list[str]:
    return sorted(_REGISTRY)
