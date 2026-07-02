"""Talk to an agent's "brain" over text — no audio, no telephony, no Pipecat.

This exercises exactly what the live pipeline's LLM stage does (same system
prompt, same tools, same handlers) using the OpenAI SDK directly. It's the
cheap, fast way to validate persona + knowledge + tool calling before spending
a cent on speech or wiring up a phone.

Requires only OPENAI_API_KEY (in your .env). Usage:

    PYTHONPATH=src python scripts/try_agent.py                  # interactive
    PYTHONPATH=src python scripts/try_agent.py "do you allow pets?"   # one-shot
    PYTHONPATH=src python scripts/try_agent.py --agent seaside-hotel
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from outreach.actions import get_action  # noqa: E402
from outreach.agents import get_registry  # noqa: E402
from outreach.config import get_settings  # noqa: E402


def _run_turn(client, model, messages, tools):
    """Run one user turn to completion, resolving any tool calls. Returns text."""
    while True:
        resp = client.chat.completions.create(model=model, messages=messages, tools=tools or None)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content})
            return msg.content

        # Record the assistant's tool-call message, then execute each tool.
        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            action = get_action(call.function.name)
            args = json.loads(call.function.arguments or "{}")
            result = action.handler(args)
            print(f"   [tool] {call.function.name}({args}) -> {result}")
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("message", nargs="*", help="One-shot message (omit for interactive).")
    parser.add_argument("--agent", default=None, help="Agent id (default: settings default).")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.openai_api_key:
        print("Set OPENAI_API_KEY in your .env to run this.")
        return 1

    try:
        from openai import OpenAI
    except ImportError:
        print("The 'openai' package isn't installed. Run: pip install -r requirements.txt")
        return 1

    registry = get_registry()
    agent = registry.by_id(args.agent or settings.default_agent_id)
    tools = [get_action(n).to_tool_spec() for n in agent.allowed_actions]
    client = OpenAI(api_key=settings.openai_api_key)
    messages = [{"role": "system", "content": agent.system_prompt()}]

    print(f"— Talking to '{agent.agent_id}' ({agent.persona.name}). Model: {settings.llm_model}")
    if agent.persona.greeting:
        print(f"{agent.persona.name}: {agent.persona.greeting}")

    if args.message:
        text = " ".join(args.message)
        print(f"You: {text}")
        messages.append({"role": "user", "content": text})
        print(f"{agent.persona.name}: {_run_turn(client, settings.llm_model, messages, tools)}")
        return 0

    print("Type your message (Ctrl-C or empty line to quit).")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            break
        messages.append({"role": "user", "content": text})
        print(f"{agent.persona.name}: {_run_turn(client, settings.llm_model, messages, tools)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
