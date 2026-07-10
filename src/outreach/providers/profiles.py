"""Build STT/LLM/TTS services for a named provider profile.

The pipeline never imports a provider SDK directly — it asks this module for
the three services given an agent config. That keeps "which vendors" a pure
configuration decision and makes adding a third profile (e.g. self-hosted)
a single function.

Pipecat imports are kept inside functions: importing this module is cheap,
so the API server / tests never need the voice dependencies installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from outreach.agents.schema import AgentConfig
from outreach.config import Settings, get_settings

# Known-good default voices (overridable per agent via persona.voice_id).
_DEFAULT_CARTESIA_VOICE = "71a7ad14-091c-4e8e-a314-022ece01c121"
_DEFAULT_SARVAM_VOICE = "anushka"  # Bulbul voice, natural Hindi + Indian English

# Rough AI-cost rates per minute (USD) per profile, used for per-call cost
# estimates shown in the dashboard. Telephony is added separately.
PROFILE_RATES: dict[str, float] = {
    "premium": 0.07,
    "budget": 0.018,
    "auto": 0.03,
}
TELEPHONY_RATE_PER_MIN = 0.014  # Twilio-ish default; Exotel India lands similar in ₹


def estimate_cost_per_minute(profile: str) -> float:
    return PROFILE_RATES.get(profile, PROFILE_RATES["premium"]) + TELEPHONY_RATE_PER_MIN


@dataclass
class ServiceBundle:
    """The three services the pipeline needs, plus metadata for logging."""

    stt: Any
    llm: Any
    tts: Any
    profile: str
    detail: str  # human-readable e.g. "deepgram/nova-3 + openai/gpt-4o-mini + cartesia"


def resolve_profile(agent: AgentConfig, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return agent.profile or settings.default_profile


def build_services(
    agent: AgentConfig,
    system_prompt: str,
    settings: Settings | None = None,
) -> ServiceBundle:
    """Build the STT/LLM/TTS trio for ``agent`` under its profile."""
    settings = settings or get_settings()
    profile = resolve_profile(agent, settings)
    if profile == "budget":
        return _build_budget(agent, system_prompt, settings)
    if profile == "premium":
        return _build_premium(agent, system_prompt, settings)
    return _build_auto(agent, system_prompt, settings)


# ----------------------------------------------------------------------
# premium: Deepgram + OpenAI + Cartesia (ElevenLabs optional for TTS)
# ----------------------------------------------------------------------

def _build_premium(agent: AgentConfig, system_prompt: str, settings: Settings) -> ServiceBundle:
    settings.require("deepgram_api_key", "openai_api_key")

    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.openai.llm import OpenAILLMService

    ov = agent.providers
    language = agent.persona.language

    # Nova-3 multilingual handles Hindi/English code-switching in one model.
    stt_model = ov.stt_model or ("nova-3" if language.startswith("en") else "nova-3-general")
    stt_kwargs: dict[str, Any] = {"api_key": settings.deepgram_api_key}
    try:
        from deepgram import LiveOptions

        stt_kwargs["live_options"] = LiveOptions(
            model=stt_model,
            language="multi" if language.startswith("hi") else language,
            smart_format=True,
        )
    except Exception:  # older/newer SDK shapes — fall back to service defaults
        pass
    stt = DeepgramSTTService(**stt_kwargs)

    llm_model = ov.llm_model or settings.llm_model
    llm = OpenAILLMService(
        api_key=settings.openai_api_key,
        settings=OpenAILLMService.Settings(
            model=llm_model,
            system_instruction=system_prompt,
        ),
    )

    # TTS: ElevenLabs if explicitly chosen and keyed, else Cartesia.
    if (ov.tts_model or "").startswith("elevenlabs") and settings.elevenlabs_api_key:
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        tts = ElevenLabsTTSService(
            api_key=settings.elevenlabs_api_key,
            voice_id=agent.persona.voice_id or "21m00Tcm4TlvDq8ikWAM",
        )
        tts_name = "elevenlabs"
    else:
        settings.require("cartesia_api_key")
        from pipecat.services.cartesia.tts import CartesiaTTSService

        tts = CartesiaTTSService(
            api_key=settings.cartesia_api_key,
            settings=CartesiaTTSService.Settings(
                voice=agent.persona.voice_id or _DEFAULT_CARTESIA_VOICE,
            ),
        )
        tts_name = "cartesia"

    return ServiceBundle(
        stt=stt,
        llm=llm,
        tts=tts,
        profile="premium",
        detail=f"deepgram/{stt_model} + openai/{llm_model} + {tts_name}",
    )


# ----------------------------------------------------------------------
# budget: Sarvam STT/TTS (India-first) + Groq or Gemini LLM
# ----------------------------------------------------------------------

def _build_budget(agent: AgentConfig, system_prompt: str, settings: Settings) -> ServiceBundle:
    settings.require("sarvam_api_key")

    try:
        from pipecat.services.sarvam.stt import SarvamSTTService
        from pipecat.services.sarvam.tts import SarvamTTSService
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The budget profile needs the Sarvam integration: "
            "pip install 'pipecat-ai[sarvam]'"
        ) from exc

    ov = agent.providers
    language = agent.persona.language
    # Sarvam expects full locale codes; sensible defaults for our market.
    sarvam_lang = {"en": "en-IN", "hi": "hi-IN"}.get(language, language)

    stt = SarvamSTTService(
        api_key=settings.sarvam_api_key,
        model=ov.stt_model or "saarika:v2.5",
        params=SarvamSTTService.InputParams(language=sarvam_lang),
    )
    tts = SarvamTTSService(
        api_key=settings.sarvam_api_key,
        voice_id=agent.persona.voice_id or _DEFAULT_SARVAM_VOICE,
        model=ov.tts_model or "bulbul:v2",
        params=SarvamTTSService.InputParams(
            language=sarvam_lang,
            # Lower temperature = steadier prosody on v3 (ignored by v2).
            temperature=0.4,
        ),
    )

    # LLM: Groq's OpenAI-compatible endpoint (fast + near-free), falling back
    # to Gemini Flash, falling back to OpenAI if that's all that's keyed.
    from pipecat.services.openai.llm import OpenAILLMService

    if settings.groq_api_key:
        llm_model = ov.llm_model or settings.budget_llm_model
        llm = OpenAILLMService(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            settings=OpenAILLMService.Settings(
                model=llm_model,
                system_instruction=system_prompt,
                # Short replies = lower time-to-full-response on a live call.
                max_tokens=200,
                temperature=0.6,
            ),
        )
        llm_name = f"groq/{llm_model}"
    elif settings.google_api_key:
        from pipecat.services.google.llm import GoogleLLMService

        llm_model = ov.llm_model or "gemini-2.5-flash-lite"
        llm = GoogleLLMService(
            api_key=settings.google_api_key,
            model=llm_model,
            system_instruction=system_prompt,
        )
        llm_name = f"google/{llm_model}"
    elif settings.openai_api_key:
        llm_model = ov.llm_model or settings.llm_model
        llm = OpenAILLMService(
            api_key=settings.openai_api_key,
            settings=OpenAILLMService.Settings(
                model=llm_model,
                system_instruction=system_prompt,
            ),
        )
        llm_name = f"openai/{llm_model}"
    else:
        raise RuntimeError(
            "Budget profile needs an LLM key: set GROQ_API_KEY (recommended, "
            "generous free tier), GOOGLE_API_KEY, or OPENAI_API_KEY."
        )

    return ServiceBundle(
        stt=stt,
        llm=llm,
        tts=tts,
        profile="budget",
        detail=f"sarvam/saarika + {llm_name} + sarvam/bulbul",
    )


# ----------------------------------------------------------------------
# auto: assemble the best stack from whatever keys are configured.
# Preference (quality-first, all free-tier friendly):
#   STT: Deepgram Nova-3  >  Sarvam Saarika
#   LLM: OpenAI  >  Groq  >  Gemini
#   TTS (English):  ElevenLabs  >  Cartesia  >  Sarvam
#   TTS (Hindi):    Sarvam      >  ElevenLabs  >  Cartesia
# Adding a key upgrades the relevant layer on the next call — no config edits.
# ----------------------------------------------------------------------

_SARVAM_V2_VOICES = {"anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh"}


def _voice_for(provider: str, voice_id: str | None) -> str | None:
    """Return voice_id only if it plausibly belongs to ``provider``."""
    if not voice_id:
        return None
    v = voice_id.strip()
    if provider == "sarvam":
        return v if v.isalpha() and v.islower() and len(v) <= 12 else None
    if provider == "cartesia":
        return v if "-" in v else None  # Cartesia voice ids are UUIDs
    if provider == "elevenlabs":
        return v if len(v) >= 15 and v.isalnum() else None
    return None


def _build_auto(agent: AgentConfig, system_prompt: str, settings: Settings) -> ServiceBundle:
    ov = agent.providers
    language = agent.persona.language
    hindi = language.startswith("hi")
    sarvam_lang = {"en": "en-IN", "hi": "hi-IN"}.get(language, language)
    parts: list[str] = []

    # ---------------- STT ----------------
    if settings.deepgram_api_key:
        from pipecat.services.deepgram.stt import DeepgramSTTService

        stt_kwargs: dict[str, Any] = {"api_key": settings.deepgram_api_key}
        try:
            from deepgram import LiveOptions

            stt_kwargs["live_options"] = LiveOptions(
                model=ov.stt_model or "nova-3",
                language="multi" if hindi else language,
                smart_format=True,
            )
        except Exception:
            pass
        stt = DeepgramSTTService(**stt_kwargs)
        parts.append("deepgram/nova-3")
    elif settings.sarvam_api_key:
        from pipecat.services.sarvam.stt import SarvamSTTService

        stt = SarvamSTTService(
            api_key=settings.sarvam_api_key,
            model=ov.stt_model or "saarika:v2.5",
            params=SarvamSTTService.InputParams(language=sarvam_lang),
        )
        parts.append("sarvam/saarika")
    else:
        raise RuntimeError("No STT key: set DEEPGRAM_API_KEY (recommended, $200 free) or SARVAM_API_KEY.")

    # ---------------- LLM ----------------
    from pipecat.services.openai.llm import OpenAILLMService

    if settings.openai_api_key:
        model = ov.llm_model or settings.llm_model
        llm = OpenAILLMService(
            api_key=settings.openai_api_key,
            settings=OpenAILLMService.Settings(
                model=model, system_instruction=system_prompt, max_tokens=200, temperature=0.6
            ),
        )
        parts.append(f"openai/{model}")
    elif settings.groq_api_key:
        model = ov.llm_model or settings.budget_llm_model
        llm = OpenAILLMService(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            settings=OpenAILLMService.Settings(
                model=model, system_instruction=system_prompt, max_tokens=200, temperature=0.6
            ),
        )
        parts.append(f"groq/{model}")
    elif settings.google_api_key:
        from pipecat.services.google.llm import GoogleLLMService

        model = ov.llm_model or "gemini-2.5-flash-lite"
        llm = GoogleLLMService(api_key=settings.google_api_key, model=model, system_instruction=system_prompt)
        parts.append(f"google/{model}")
    else:
        raise RuntimeError("No LLM key: set GROQ_API_KEY (free), OPENAI_API_KEY or GOOGLE_API_KEY.")

    # ---------------- TTS ----------------
    hint = (ov.tts_model or "").lower()
    if "bulbul" in hint:
        order = ["sarvam"]
    elif hint.startswith("eleven"):
        order = ["elevenlabs"]
    elif "sonic" in hint or "cartesia" in hint:
        order = ["cartesia"]
    else:
        # Sarvam first by default: unlimited free-credit voice. Premium TTS
        # (ElevenLabs/Cartesia) is OPT-IN per agent via providers.tts_model,
        # so limited free-tier credits are only spent when explicitly chosen
        # (e.g. for a sales demo).
        order = ["sarvam", "elevenlabs", "cartesia"]

    tts = None
    for choice in order:
        if choice == "elevenlabs" and settings.elevenlabs_api_key:
            from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

            tts = ElevenLabsTTSService(
                api_key=settings.elevenlabs_api_key,
                voice_id=_voice_for("elevenlabs", agent.persona.voice_id) or "21m00Tcm4TlvDq8ikWAM",
                model="eleven_flash_v2_5",  # lowest latency EL model
            )
            parts.append("elevenlabs/flash-2.5")
            break
        if choice == "cartesia" and settings.cartesia_api_key:
            from pipecat.services.cartesia.tts import CartesiaTTSService

            tts = CartesiaTTSService(
                api_key=settings.cartesia_api_key,
                settings=CartesiaTTSService.Settings(
                    voice=_voice_for("cartesia", agent.persona.voice_id) or _DEFAULT_CARTESIA_VOICE,
                ),
            )
            parts.append("cartesia/sonic")
            break
        if choice == "sarvam" and settings.sarvam_api_key:
            from pipecat.services.sarvam.tts import SarvamTTSService

            voice = _voice_for("sarvam", agent.persona.voice_id)
            model = "bulbul:v2" if voice in _SARVAM_V2_VOICES else "bulbul:v3-beta"
            tts = SarvamTTSService(
                api_key=settings.sarvam_api_key,
                voice_id=voice or "neha",
                model=model,
                params=SarvamTTSService.InputParams(language=sarvam_lang, temperature=0.4),
            )
            parts.append(f"sarvam/{model}")
            break
    if tts is None:
        raise RuntimeError("No TTS key: set ELEVENLABS_API_KEY, CARTESIA_API_KEY or SARVAM_API_KEY.")

    return ServiceBundle(stt=stt, llm=llm, tts=tts, profile="auto", detail=" + ".join(parts))
