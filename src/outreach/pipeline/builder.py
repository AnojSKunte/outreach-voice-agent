"""Assemble a cascaded voice pipeline from an AgentConfig.

This is the heart of the runtime: given a resolved agent and a Pipecat
transport (telephony in production, or a local/test transport), it builds the
STT -> LLM -> TTS pipeline for the agent's provider profile, wires the
agent's permitted tools, and returns a ready-to-run :class:`VoiceSession`.

Provider choices come from ``outreach.providers`` (premium/budget profiles
with per-agent overrides), so swapping vendors is configuration, not code.

Transcripts: rather than a dedicated processor (removed in Pipecat 1.x), the
conversation is read back from the ``LLMContext`` after the call — the
context aggregators keep it in sync with what was actually said.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

# --- Pipecat imports (heavy; only loaded when building a session) ---
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)

from outreach.agents.schema import AgentConfig
from outreach.config import Settings, get_settings
from outreach.pipeline.tools import build_tools_schema, register_actions
from outreach.providers.profiles import build_services


def make_vad() -> SileroVADAnalyzer:
    """Tuned VAD: responsive turn-taking without cutting callers off
    mid-pause. Shared by the transport and the context aggregator."""
    return SileroVADAnalyzer(
        params=VADParams(confidence=0.7, start_secs=0.2, stop_secs=0.3)
    )


@dataclass
class VoiceSession:
    """Everything needed to run one call, plus a ``run()`` convenience."""

    agent: AgentConfig
    task: PipelineTask
    context: LLMContext
    profile: str

    async def run(self) -> None:
        """Run the pipeline to completion (until the transport disconnects)."""
        runner = PipelineRunner(handle_sigint=False)
        await runner.run(self.task)

    def snapshot_transcript(self) -> list[dict[str, Any]]:
        """Extract the user/assistant conversation from the LLM context."""
        out: list[dict[str, Any]] = []
        try:
            messages = self.context.get_messages()
        except Exception:  # pragma: no cover — context API drift safety net
            logger.exception("could not read messages from LLMContext")
            return out
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if role not in ("user", "assistant") or not content:
                continue
            if isinstance(content, list):  # multi-part content
                content = " ".join(
                    str(p.get("text", "")) for p in content if isinstance(p, dict)
                )
            content = str(content).strip()
            if content:
                out.append({"role": role, "content": content})
        return out


def build_voice_session(
    agent: AgentConfig,
    transport,
    settings: Settings | None = None,
    call_context: dict[str, Any] | None = None,
    telephony: bool = True,
) -> VoiceSession:
    """Build a runnable voice session for ``agent`` over ``transport``.

    ``transport`` is any Pipecat transport exposing ``input()``/``output()``
    and the ``on_client_connected`` / ``on_client_disconnected`` events
    (e.g. ``FastAPIWebsocketTransport`` for Twilio/Exotel).

    ``call_context`` carries per-call facts: ``direction`` ("inbound" |
    "outbound"), ``goal`` and ``lead`` for outbound campaign calls.
    """
    settings = settings or get_settings()
    ctx = call_context or {}
    direction = ctx.get("direction", "inbound")

    system_prompt = agent.system_prompt(ctx)
    bundle = build_services(agent, system_prompt, settings)
    logger.info(f"pipeline for '{agent.agent_id}': profile={bundle.profile} [{bundle.detail}]")

    # --- Tools the agent is allowed to use ---
    tools = build_tools_schema(agent.allowed_actions)
    register_actions(bundle.llm, agent.allowed_actions)

    # --- Context + aggregators (VAD drives turn-taking / barge-in) ---
    context = LLMContext(tools=tools)
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=make_vad()),
    )

    pipeline = Pipeline(
        [
            transport.input(),        # caller audio in
            bundle.stt,               # speech -> text
            aggregators.user(),       # add user turn to context
            bundle.llm,               # reason + maybe call a tool
            bundle.tts,               # text -> speech
            transport.output(),       # audio back to caller
            aggregators.assistant(),  # record what was actually said
        ]
    )

    params = PipelineParams(enable_metrics=True, enable_usage_metrics=True)
    if telephony:
        # Twilio/Exotel media streams are 8 kHz mono.
        params.audio_in_sample_rate = 8000
        params.audio_out_sample_rate = 8000

    task = PipelineTask(pipeline, params=params)

    session = VoiceSession(
        agent=agent, task=task, context=context, profile=bundle.profile
    )

    # --- First words on the call ---
    p = agent.persona
    if direction == "outbound":
        first_line = agent.opening_line_for(ctx.get("lead"))
    else:
        first_line = p.greeting

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, _client):  # pragma: no cover - runtime glue
        logger.info(f"call connected -> agent '{agent.agent_id}' ({direction})")
        if first_line:
            # Speak the line verbatim (deterministic, low latency) and record
            # it so the model's history matches what the caller heard.
            await task.queue_frames([TTSSpeakFrame(first_line)])
            context.add_message({"role": "assistant", "content": first_line})
        else:
            hint = (
                "The callee just answered your outbound call. Open the "
                "conversation naturally."
                if direction == "outbound"
                else "Greet the caller briefly and ask how you can help."
            )
            context.add_message({"role": "developer", "content": hint})
            await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, _client):  # pragma: no cover - runtime glue
        logger.info(f"call disconnected -> agent '{agent.agent_id}'")
        await task.cancel()

    return session
