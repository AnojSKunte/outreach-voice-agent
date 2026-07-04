"""Confirm every Pipecat symbol the project uses resolves after install.

Pipecat is a fast-moving library; this catches a version/module mismatch
instantly (no API keys, no audio models loaded) so you find out at install
time, not on your first phone call.

    PYTHONPATH=src python scripts/smoke_imports.py
"""

from __future__ import annotations

CHECKS = [
    ("pipecat.pipeline.pipeline", "Pipeline"),
    ("pipecat.pipeline.task", "PipelineTask"),
    ("pipecat.pipeline.task", "PipelineParams"),
    ("pipecat.pipeline.runner", "PipelineRunner"),
    ("pipecat.frames.frames", "LLMRunFrame"),
    ("pipecat.frames.frames", "TTSSpeakFrame"),
    ("pipecat.audio.vad.silero", "SileroVADAnalyzer"),
    ("pipecat.processors.aggregators.llm_context", "LLMContext"),
    ("pipecat.processors.aggregators.llm_response_universal", "LLMContextAggregatorPair"),
    ("pipecat.processors.aggregators.llm_response_universal", "LLMUserAggregatorParams"),
    ("pipecat.adapters.schemas.function_schema", "FunctionSchema"),
    ("pipecat.adapters.schemas.tools_schema", "ToolsSchema"),
    ("pipecat.services.llm_service", "FunctionCallParams"),
    ("pipecat.services.deepgram.stt", "DeepgramSTTService"),
    ("pipecat.services.openai.llm", "OpenAILLMService"),
    ("pipecat.services.cartesia.tts", "CartesiaTTSService"),
    # Telephony:
    ("pipecat.transports.websocket.fastapi", "FastAPIWebsocketTransport"),
    ("pipecat.transports.websocket.fastapi", "FastAPIWebsocketParams"),
    ("pipecat.serializers.twilio", "TwilioFrameSerializer"),
    ("pipecat.serializers.exotel", "ExotelFrameSerializer"),
    ("pipecat.runner.utils", "parse_telephony_websocket"),
    # Budget profile (Sarvam = India Hindi/Hinglish STT+TTS):
    ("pipecat.services.sarvam.stt", "SarvamSTTService"),
    ("pipecat.services.sarvam.tts", "SarvamTTSService"),
]


def main() -> int:
    import importlib

    failures = []
    for module, symbol in CHECKS:
        try:
            mod = importlib.import_module(module)
            getattr(mod, symbol)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"  x {module}.{symbol}  ({type(exc).__name__}: {exc})")

    if failures:
        print("Import check FAILED - Pipecat API may have shifted:")
        print("\n".join(failures))
        print("\nCheck the installed pipecat-ai version against requirements.txt.")
        return 1

    import pipecat

    print(f"ok: all {len(CHECKS)} Pipecat imports resolved (pipecat-ai {getattr(pipecat, '__version__', '?')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
