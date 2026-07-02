"""Bridge our provider-neutral action registry into Pipecat's tool calling.

Our actions (``outreach.actions``) are declared once as data. Here we convert
the ones an agent is allowed to use into Pipecat ``FunctionSchema`` objects and
register async handlers that run the action and return its result via
``params.result_callback``. The LLM only ever sees the tools its agent config
permits.
"""

from __future__ import annotations

import inspect
from typing import Iterable

from loguru import logger

# Pipecat imports kept inside this module (heavy deps); see pipeline/__init__.py.
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams, LLMService

from outreach.actions import get_action


def build_tools_schema(action_names: Iterable[str]) -> ToolsSchema:
    """Convert allowed action names into a Pipecat ToolsSchema."""
    schemas: list[FunctionSchema] = []
    for name in action_names:
        action = get_action(name)
        params = action.parameters or {}
        schemas.append(
            FunctionSchema(
                name=action.name,
                description=action.description,
                properties=params.get("properties", {}),
                required=params.get("required", []),
            )
        )
    return ToolsSchema(standard_tools=schemas)


def _make_handler(action_name: str):
    action = get_action(action_name)

    async def handler(params: FunctionCallParams) -> None:
        try:
            result = action.handler(dict(params.arguments))
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 — never let a tool crash the call
            logger.exception(f"action '{action_name}' raised")
            result = {"error": f"The {action_name} action failed: {exc}"}
        await params.result_callback(result)

    return handler


def register_actions(llm: LLMService, action_names: Iterable[str]) -> None:
    """Register a handler on the LLM service for each allowed action."""
    for name in action_names:
        llm.register_function(name, _make_handler(name))
