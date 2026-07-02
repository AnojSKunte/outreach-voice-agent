"""Assemble a cascaded voice pipeline from an AgentConfig.

This is the heart of the runtime: given a resolved agent and a Pipecat
transport (telephony in production, or a local/test transport), it builds the
STT -> LLM -> TTS pipeline for the agent's provider profile, wires the
agent's permitted tools, captures the transcript, and returns a ready-to-run
:class:`VoiceSession`.

Provider choices come from ``outreach.providers`` (premium/budget profiles
with per-agent overrides), so swapping vendors is configuration, not code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger

# --- Pipecat imports (heavy; only loaded when building a session) ---
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.transcript_processor import TranscriptProcessor

from outreach.agents.schema import AgentConfig
from outreach.config import Settings, get_settings
from outreach.pipeline.tools import build_tools_schema, register_actions
from outreach.providers.profiles import build_services


@dataclass
class VoiceSession:
    """Everything needed to run one call, plus a ``run()`` convenience."""

    agent: AgentConfig
    task: PipelineTask
    context: LLMContext
    profile: str
    # Filled live during the call: [{"role","content","t"}, ...]
    transcript: list[dict[str, Any]] = field(default_factory=list)

    async def run(self) -> None:
        """Run the pipeline to completion (until the transport disconnects)."""
        runner = PipelineRunner(handle_sigint=False)
        await runner.run(self.task)


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
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    # --- Transcript capture (persisted by the call lifecycle service) ---
    transcript_proc = TranscriptProcessor()

    pipeline = Pipeline(
        [
            transport.input(),            # caller audio in
            bundle.stt,                   # speech -> text
            transcript_proc.user(),       # record caller turns
            aggregators.user(),           # add user turn to context
            bundle.llm,                   # reason + maybe call a tool
            bundle.tts,                   # text -> speech
            transport.output(),           # audio back to caller
            transcript_proc.assistant(),  # record what was actually said
            aggregators.assistant(),
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

    @transcript_proc.event_handler("on_transcript_update")
    async def _on_transcript(_proc, frame):  # pragma: no cover - runtime glue
        for msg in frame.messages:
            session.transcript.append(
                {
                    "role": msg.role,
                    "content": msg.content,
                    "t": getattr(msg, "timestamp", None)
                    or datetime.now(timezone.utc).isoformat(),
                }
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
            session.transcript.append(
                {
                    "role": "assistant",
                    "content": first_line,
                    "t": datetime.now(timezone.utc).isoformat(),
                }
            )
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
