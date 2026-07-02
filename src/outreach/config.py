"""Process-wide settings, loaded from environment variables.

These are *infrastructure* secrets and defaults shared across all tenants
(provider API keys, server binding). Per-client behaviour lives in agent
config files instead — see ``outreach.agents``.

Nothing here ever hard-codes a secret; values come from the environment
(a local ``.env`` in development, host env vars in production).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Provider credentials (all optional at import time so the app can boot
    # and serve health checks before every key is set; components validate
    # the ones they actually need when used).
    # ------------------------------------------------------------------

    # Premium profile providers
    deepgram_api_key: str | None = Field(default=None, alias="DEEPGRAM_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    cartesia_api_key: str | None = Field(default=None, alias="CARTESIA_API_KEY")
    elevenlabs_api_key: str | None = Field(default=None, alias="ELEVENLABS_API_KEY")

    # Budget profile providers (India-first: Sarvam for Hindi/Hinglish)
    sarvam_api_key: str | None = Field(default=None, alias="SARVAM_API_KEY")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")

    # Telephony
    telephony_provider: str = Field(default="twilio", alias="TELEPHONY_PROVIDER")  # twilio | exotel
    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = Field(default=None, alias="TWILIO_AUTH_TOKEN")
    twilio_from_number: str | None = Field(default=None, alias="TWILIO_FROM_NUMBER")
    exotel_sid: str | None = Field(default=None, alias="EXOTEL_SID")
    exotel_api_key: str | None = Field(default=None, alias="EXOTEL_API_KEY")
    exotel_api_token: str | None = Field(default=None, alias="EXOTEL_API_TOKEN")
    exotel_from_number: str | None = Field(default=None, alias="EXOTEL_FROM_NUMBER")
    exotel_subdomain: str = Field(default="api.exotel.com", alias="EXOTEL_SUBDOMAIN")

    # ------------------------------------------------------------------
    # Model / profile defaults (an agent config may override per client)
    # ------------------------------------------------------------------
    # Which provider profile agents use unless they pin one: premium | budget
    default_profile: str = Field(default="premium", alias="DEFAULT_PROFILE")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    budget_llm_model: str = Field(default="llama-3.3-70b-versatile", alias="BUDGET_LLM_MODEL")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    # SQLite by default (zero-config); set a postgres:// URL in production.
    database_url: str = Field(default="sqlite:///outreach.db", alias="DATABASE_URL")

    # ------------------------------------------------------------------
    # Server / API
    # ------------------------------------------------------------------
    public_host: str | None = Field(default=None, alias="PUBLIC_HOST")
    port: int = Field(default=8000, alias="PORT")
    # Shared secret for the REST API (X-API-Key header). If unset, the API
    # is open — fine locally, never in production.
    outreach_api_key: str | None = Field(default=None, alias="OUTREACH_API_KEY")

    # Outbound webhooks: comma-separated URLs that receive call lifecycle
    # events, signed with WEBHOOK_SECRET (HMAC-SHA256 in X-Outreach-Signature).
    webhook_urls: str = Field(default="", alias="WEBHOOK_URLS")
    webhook_secret: str | None = Field(default=None, alias="WEBHOOK_SECRET")

    # Agent used for calls to an unmapped number (handy on a trial number).
    default_agent_id: str = Field(default="seaside-hotel", alias="DEFAULT_AGENT_ID")

    # ------------------------------------------------------------------
    # Call guardrails
    # ------------------------------------------------------------------
    max_call_seconds: int = Field(default=600, alias="MAX_CALL_SECONDS")
    # Hang up after this many seconds of silence from the caller.
    idle_timeout_seconds: int = Field(default=30, alias="IDLE_TIMEOUT_SECONDS")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def webhook_url_list(self) -> list[str]:
        return [u.strip() for u in self.webhook_urls.split(",") if u.strip()]

    def require(self, *names: str) -> None:
        """Raise a clear error if any named credential is missing.

        Called by components when they're about to need a key, so failures
        say *what to set* rather than surfacing a vague auth error mid-call.
        """
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            env_names = [n.upper() for n in missing]
            raise RuntimeError(
                "Missing required environment variable(s): "
                + ", ".join(env_names)
                + ". Set them in your .env (local) or host env vars (prod)."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings. Use this everywhere instead of constructing
    ``Settings()`` directly, so the environment is read once."""
    return Settings()
