"""The voice pipeline layer.

Assembles a cascaded STT -> LLM -> TTS Pipecat pipeline from an AgentConfig.
Pipecat (and the provider SDKs) are imported lazily inside ``builder`` /
``tools`` so that importing :mod:`outreach.agents` / :mod:`outreach.actions`
(the foundation) never requires the heavy voice dependencies to be installed.
"""

__all__ = ["build_voice_session", "VoiceSession"]


def __getattr__(name: str):  # PEP 562 lazy re-export
    if name in __all__:
        from outreach.pipeline.builder import VoiceSession, build_voice_session

        return {"build_voice_session": build_voice_session, "VoiceSession": VoiceSession}[name]
    raise AttributeError(name)
