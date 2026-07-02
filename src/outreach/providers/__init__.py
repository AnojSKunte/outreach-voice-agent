"""Provider profiles: swappable STT/LLM/TTS stacks.

Two named profiles ship out of the box:

* ``premium`` — Deepgram Nova-3 STT + OpenAI LLM + Cartesia TTS.
  Best quality/latency; ~$0.05–0.09 per minute in AI costs.
* ``budget``  — Sarvam Saarika STT + Groq (or Gemini) LLM + Sarvam Bulbul TTS.
  India-first (best Hindi/Hinglish), roughly ₹1–2 per minute all-in AI cost.

Each agent picks a profile in its config (or inherits the platform default),
and can still override individual models via ``providers:`` overrides.
"""

__all__ = ["build_services", "estimate_cost_per_minute", "PROFILE_RATES"]

from outreach.providers.profiles import (  # noqa: E402
    PROFILE_RATES,
    estimate_cost_per_minute,
)


def __getattr__(name: str):  # lazy: building services imports pipecat (heavy)
    if name == "build_services":
        from outreach.providers.profiles import build_services

        return build_services
    raise AttributeError(name)
