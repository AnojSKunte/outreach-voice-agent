"""Process-wide settings, loaded from environment variables.

These are *infrastructure* secrets and defaults shared across all tenants
(provider API keys, server binding). Per-client behaviour lives in agent
config files instead — see ``outreach.agents``.

Nothing here ever hard-codes a secret; values come from the environment
(a local ``.env`` in development, Render env vars in production).
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

    # --- Provider credentials (optional at import time so the app can boot
    # and serve health checks even before every key is set; the pipeline
    # validates the ones it actually needs when a call starts). ---
    deepgram_api_key: str | None = Field(default=None, alias="DEEPGRAM_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    cartesia_api_key: str | None = Field(default=None, alias="CARTESIA_API_KEY")
    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = Field(default=None, alias="TWILIO_AUTH_TOKEN")

    # --- Model defaults (an agent config may override per client). ---
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    # --- Server ---
    public_host: str | None = Field(default=None, alias="PUBLIC_HOST")
    port: int = Field(default=8000, alias="PORT")

    # Agent used for calls to an unmapped number (handy on a trial number).
    default_agent_id: str = Field(default="seaside-hotel", alias="DEFAULT_AGENT_ID")

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
                + ". Set them in your .env (local) or Render env vars (prod)."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings. Use this everywhere instead of constructing
    ``Settings()`` directly, so the environment is read once."""
    return Settings()
