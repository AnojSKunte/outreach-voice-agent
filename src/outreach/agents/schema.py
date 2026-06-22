"""The structured "context file" for a client agent.

This is the heart of the multi-tenant design: everything that makes one
client's agent different from another's is declared here as data, validated
by pydantic, and loaded at call time. Phase 1 ships one of these (Seaside
Hotel); adding client #2 is adding another YAML file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class PersonaConfig(BaseModel):
    """How the agent sounds and carries itself."""

    name: str = Field(description="Agent's name, e.g. 'Mia'.")
    role: str = Field(description="One line: who the agent is, e.g. 'reservations assistant for Seaside Hotel'.")
    tone: str = Field(
        default="warm, concise, professional",
        description="Style guidance woven into the system prompt.",
    )
    language: str = Field(default="en", description="Primary BCP-47 language code.")
    voice_id: str | None = Field(
        default=None,
        description="TTS provider voice id. None = use provider default.",
    )
    greeting: str | None = Field(
        default=None,
        description="Optional first line spoken when the call connects.",
    )


class ProviderOverrides(BaseModel):
    """Optional per-client overrides of the default STT/LLM/TTS choices.

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


class AgentConfig(BaseModel):
    """A complete client agent definition."""

    agent_id: str = Field(description="Stable slug, matches the agents/<id>/ folder.")
    client_name: str
    # The dialed number this agent answers (E.164, e.g. +14155550123).
    # May be empty during early testing on a shared trial number.
    phone_number: str | None = None

    persona: PersonaConfig
    # Free-form knowledge: FAQs, policies, pricing. Either inline text here,
    # or loaded from a sibling knowledge file (see AgentRegistry).
    knowledge_base: str = Field(default="", description="Facts the agent may rely on.")
    # Names of actions (tools) this agent is permitted to call. Must exist in
    # the action registry; unknown names are rejected at load time.
    allowed_actions: list[str] = Field(default_factory=list)

    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
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

    def system_prompt(self) -> str:
        """Render the persona + knowledge into a system prompt.

        Centralised here so prompt construction is consistent across every
        tenant and easy to evolve in one place.
        """
        p = self.persona
        lines = [
            f"You are {p.name}, the {p.role}.",
            f"Speak in a {p.tone} manner. You are on a live phone call, so keep "
            "replies short and natural — one or two sentences — and never read "
            "out lists, markdown, or URLs.",
            "Only answer using the information below. If you don't know "
            "something, say so plainly and offer to connect the caller to a person.",
        ]
        if self.knowledge_base.strip():
            lines.append("\n--- What you know ---\n" + self.knowledge_base.strip())
        if self.allowed_actions:
            lines.append(
                "\nYou can take actions on the caller's behalf using the tools "
                "provided. Use them when appropriate rather than guessing."
            )
        return "\n".join(lines)


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
