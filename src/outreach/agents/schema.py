"""The structured "context file" for a client agent.

This is the heart of the multi-tenant design: everything that makes one
client's agent different from another's is declared here as data, validated
by pydantic, and loaded at call time. Adding a client is adding a YAML file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PersonaConfig(BaseModel):
    """How the agent sounds and carries itself."""

    name: str = Field(description="Agent's name, e.g. 'Mia'.")
    role: str = Field(description="One line: who the agent is, e.g. 'reservations assistant for Seaside Hotel'.")
    tone: str = Field(
        default="warm, concise, professional",
        description="Style guidance woven into the system prompt.",
    )
    language: str = Field(
        default="en",
        description="Primary BCP-47 language code, e.g. 'en', 'hi', 'en-IN'. "
        "Drives STT/TTS language selection.",
    )
    voice_id: str | None = Field(
        default=None,
        description="TTS provider voice id. None = use profile default.",
    )
    greeting: str | None = Field(
        default=None,
        description="Optional first line spoken when an INBOUND call connects.",
    )


class ProviderOverrides(BaseModel):
    """Optional per-client overrides of the profile's STT/LLM/TTS choices.

    Keeps the platform provider-agnostic: a premium client can use a higher
    quality (pricier) voice while the default stack stays cost-optimised.
    """

    stt_model: str | None = None
    llm_model: str | None = None
    tts_model: str | None = None


class EscalationConfig(BaseModel):
    """What 'hand off to a human' means for this client."""

    enabled: bool = True
    # E.164 number to warm/cold transfer to, or None to just take a message.
    transfer_number: str | None = None
    message: str = Field(
        default="Let me connect you with a member of our team.",
        description="Said to the caller before handing off.",
    )


class OutboundConfig(BaseModel):
    """How this agent behaves on OUTBOUND calls (campaigns / API-triggered).

    The campaign supplies the concrete goal and any per-lead variables; this
    block sets the defaults so a bare 'call this lead' still works.
    """

    # Default objective if the campaign doesn't set one.
    goal: str = Field(
        default="",
        description="What the agent is trying to achieve on an outbound call, "
        "e.g. 'qualify the lead and book a follow-up visit'.",
    )
    opening_line: str | None = Field(
        default=None,
        description="First line spoken when the callee answers. Supports "
        "{lead_name}, {company}, {agent_name} placeholders.",
    )
    # What to do when an answering machine is detected: hangup | leave_message
    voicemail_action: str = Field(default="hangup")
    voicemail_message: str | None = Field(
        default=None,
        description="Message to leave when voicemail_action=leave_message.",
    )


class ComplianceConfig(BaseModel):
    """Regulatory behaviour. AI disclosure is required or strongly advised in
    a growing set of jurisdictions (and it builds caller trust)."""

    disclose_ai: bool = Field(
        default=True,
        description="Agent states it is an AI assistant near the start of a call.",
    )
    disclosure_line: str = Field(
        default="Just so you know, I'm an AI assistant.",
    )
    # Recording notice, if you record calls in a consent jurisdiction.
    recording_notice: str | None = None


class AgentConfig(BaseModel):
    """A complete client agent definition."""

    agent_id: str = Field(description="Stable slug, matches the agents/<id>/ folder.")
    client_name: str
    # The dialed number this agent answers (E.164, e.g. +14155550123).
    # May be empty during early testing on a shared trial number.
    phone_number: str | None = None

    # Which provider profile runs this agent: 'premium' | 'budget'.
    # None = the platform default (DEFAULT_PROFILE env var).
    profile: str | None = Field(default=None)

    persona: PersonaConfig
    # Free-form knowledge: FAQs, policies, pricing. Either inline text here,
    # or loaded from a sibling knowledge file (see AgentRegistry).
    knowledge_base: str = Field(default="", description="Facts the agent may rely on.")
    # Names of actions (tools) this agent is permitted to call. Must exist in
    # the action registry; unknown names are rejected at load time.
    allowed_actions: list[str] = Field(default_factory=list)

    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    outbound: OutboundConfig = Field(default_factory=OutboundConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    providers: ProviderOverrides = Field(default_factory=ProviderOverrides)

    @field_validator("phone_number")
    @classmethod
    def _normalise_number(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().replace(" ", "").replace("-", "")
        if v and not v.startswith("+"):
            raise ValueError("phone_number must be E.164 and start with '+' (e.g. +14155550123).")
        return v or None

    @field_validator("profile")
    @classmethod
    def _check_profile(cls, v: str | None) -> str | None:
        if v is not None and v not in ("premium", "budget"):
            raise ValueError("profile must be 'premium' or 'budget'.")
        return v

    # ------------------------------------------------------------------
    # prompt construction
    # ------------------------------------------------------------------

    def system_prompt(self, call_context: dict[str, Any] | None = None) -> str:
        """Render the persona + knowledge (+ per-call context) into a system
        prompt. Centralised here so prompt construction is consistent across
        every tenant and easy to evolve in one place.

        ``call_context`` carries call-specific facts for outbound calls:
        ``direction``, ``goal``, ``lead`` (dict of name/company/notes/custom).
        """
        p = self.persona
        lines = [
            f"You are {p.name}, the {p.role}.",
            f"Speak in a {p.tone} manner. You are on a live phone call, so keep "
            "replies short and natural — one or two sentences — and never read "
            "out lists, markdown, or URLs. Numbers, dates and prices should be "
            "spoken the way a person would say them aloud.",
        ]
        if p.language.startswith("hi"):
            lines.append(
                "Speak Hindi by default, mixing in English naturally (Hinglish) "
                "the way the caller does. Mirror the caller's language."
            )
        elif p.language not in ("en", "en-US", "en-GB"):
            lines.append(f"Speak in the language with BCP-47 code '{p.language}'.")

        if self.compliance.disclose_ai:
            lines.append(
                "Early in the call, briefly disclose that you are an AI "
                f'assistant (e.g. "{self.compliance.disclosure_line}"). Do not '
                "dwell on it."
            )

        ctx = call_context or {}
        if ctx.get("direction") == "outbound":
            goal = ctx.get("goal") or self.outbound.goal
            lines.append(
                "\nThis is an OUTBOUND call that you placed. Confirm you are "
                "speaking with the right person before getting into details. "
                "If they say it's a bad time, offer to call back and end "
                "politely. Never be pushy; one clear ask per call."
            )
            if goal:
                lines.append(f"Your objective on this call: {goal}")
            lead = ctx.get("lead") or {}
            known = {k: v for k, v in lead.items() if v}
            if known:
                facts = "; ".join(f"{k}: {v}" for k, v in known.items())
                lines.append(f"What you know about the person you're calling: {facts}")
        else:
            lines.append(
                "Only answer using the information below. If you don't know "
                "something, say so plainly and offer to connect the caller to a person."
            )

        if self.knowledge_base.strip():
            lines.append("\n--- What you know ---\n" + self.knowledge_base.strip())
        if self.allowed_actions:
            lines.append(
                "\nYou can take actions on the caller's behalf using the tools "
                "provided. Use them when appropriate rather than guessing."
            )
        return "\n".join(lines)

    def opening_line_for(self, lead: dict[str, Any] | None = None) -> str | None:
        """Render the outbound opening line with lead placeholders filled."""
        template = self.outbound.opening_line
        if not template:
            return None
        lead = lead or {}
        return (
            template.replace("{lead_name}", str(lead.get("name") or "there"))
            .replace("{company}", str(lead.get("company") or ""))
            .replace("{agent_name}", self.persona.name)
            .strip()
        )


def load_agent_config(config_path: Path, knowledge_dir: Path | None = None) -> AgentConfig:
    """Load and validate one AgentConfig from a YAML file.

    If the YAML sets ``knowledge_file`` (relative to the config), its contents
    are read into ``knowledge_base`` so long policy/FAQ text can live in a
    readable markdown file instead of being crammed into YAML.
    """
    import yaml

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    knowledge_file = raw.pop("knowledge_file", None)
    if knowledge_file:
        base = knowledge_dir or config_path.parent
        kb_path = (base / knowledge_file).resolve()
        raw["knowledge_base"] = kb_path.read_text(encoding="utf-8")

    return AgentConfig.model_validate(raw)
