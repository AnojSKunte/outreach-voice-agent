"""Talk to any agent in TEXT, before spending a rupee on voice or telephony.

Runs the same system prompt + tools the live call uses, minus STT/TTS.

    PYTHONPATH=src python scripts/chat_with_agent.py seaside-hotel
    PYTHONPATH=src python scripts/chat_with_agent.py lead-gen-demo --outbound

LLM resolution order: OPENAI_API_KEY -> GROQ_API_KEY -> GOOGLE_API_KEY.
With NO key at all it drops to a stub that echoes tool availability — useful
to check prompts/configs load, not conversation quality.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys

from outreach.actions import get_action
from outreach.agents import get_registry
from outreach.config import get_settings


def _client_and_model():
    settings = get_settings()
    try:
        from openai import OpenAI
    except ImportError:
        return None, None
    if settings.openai_api_key:
        return OpenAI(api_key=settings.openai_api_key), settings.llm_model
    if settings.groq_api_key:
        return (
            OpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1"),
            settings.budget_llm_model,
        )
    if settings.google_api_key:
        return (
            OpenAI(
                api_key=settings.google_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            ),
            "gemini-2.5-flash",
        )
    return None, None


async def _run_tool(name: str, args: dict):
    result = get_action(name).handler(args)
    if inspect.isawaitable(result):
        result = await result
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agent_id")
    parser.add_argument("--outbound", action="store_true", help="simulate an outbound call")
    parser.add_argument("--lead-name", default="Rahul")
    ns = parser.parse_args()

    agent = get_registry().by_id(ns.agent_id)
    ctx = (
        {"direction": "outbound", "lead": {"name": ns.lead_name, "company": "—"}}
        if ns.outbound
        else {"direction": "inbound"}
    )
    system = agent.system_prompt(ctx)
    tools = [get_action(a).to_tool_spec() for a in agent.allowed_actions]

    client, model = _client_and_model()
    print(f"— chatting with '{agent.agent_id}' ({'outbound' if ns.outbound else 'inbound'}) —")
    if client is None:
        print("!! no LLM key found (OPENAI/GROQ/GOOGLE_API_KEY) — config check only.\n")
        print("System prompt the agent would use:\n")
        print(system)
        print(f"\nTools available: {agent.allowed_actions}")
        return 0
    print(f"   model: {model} | tools: {agent.allowed_actions} | Ctrl-C to quit\n")

    messages: list[dict] = [{"role": "system", "content": system}]
    opening = agent.opening_line_for(ctx.get("lead")) if ns.outbound else agent.persona.greeting
    if opening:
        messages.append({"role": "assistant", "content": opening})
        print(f"🤖 {opening}")

    import asyncio

    while True:
        try:
            user = input("👤 ")
        except (KeyboardInterrupt, EOFError):
            print("\nbye")
            return 0
        messages.append({"role": "user", "content": user})

        while True:  # loop to satisfy tool calls
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools or None,
            )
            msg = resp.choices[0].message
            if msg.tool_calls:
                messages.append(msg.model_dump(exclude_none=True))
                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments or "{}")
                    result = asyncio.run(_run_tool(tc.function.name, args))
                    print(f"   ⚙️  {tc.function.name}({args}) -> {result}")
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)}
                    )
                continue
            print(f"🤖 {msg.content}")
            messages.append({"role": "assistant", "content": msg.content})
            break


if __name__ == "__main__":
    sys.exit(main())
