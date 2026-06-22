"""Outreach — a multi-tenant voice AI calling agent platform.

Phase 1 proves a single inbound call end-to-end, but the package is laid out
so that adding more clients is dropping in a new agent config, not a rewrite:

    outreach.config    -> process-wide settings from environment variables
    outreach.agents    -> per-client AgentConfig schema + number->agent registry
    outreach.actions   -> tools the LLM may call during a conversation
    outreach.pipeline   -> assembles the STT->LLM->TTS pipeline from an AgentConfig
    outreach.server    -> telephony webhook + media-stream websocket (web service)
"""

__version__ = "0.1.0"
