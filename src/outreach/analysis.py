"""Post-call analysis: summary, outcome tag, structured extraction.

Runs after every call with a transcript. Uses whichever LLM key is available
(OpenAI, then Groq, then none). Failure or absence of a key degrades
gracefully — the call record simply has no summary.

The outcome tag drives lead-status updates in campaigns:
    interested | not_interested | callback | voicemail | wrong_number |
    no_outcome | converted | dnc_request
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from outreach.config import get_settings

OUTCOMES = (
    "interested",
    "not_interested",
    "callback",
    "voicemail",
    "wrong_number",
    "converted",
    "dnc_request",
    "no_outcome",
)

_PROMPT = """You analyze transcripts of phone calls made by an AI voice agent.
Return STRICT JSON with keys:
  "summary": 2-3 sentence factual summary of the call,
  "outcome": one of {outcomes},
  "extracted": object with any concrete details worth saving
               (e.g. callback_time, email, budget, objections, booking details).
If the callee asked not to be called again, outcome MUST be "dnc_request".
Transcript:
{transcript}
"""


def _transcript_text(transcript: list[dict[str, Any]]) -> str:
    lines = []
    for turn in transcript or []:
        role = "Agent" if turn.get("role") == "assistant" else "Caller"
        lines.append(f"{role}: {turn.get('content', '')}")
    return "\n".join(lines)


def _llm_client():
    """Return (client, model) for whichever provider is keyed, else None."""
    settings = get_settings()
    try:
        from openai import OpenAI
    except ImportError:
        return None
    if settings.openai_api_key:
        return OpenAI(api_key=settings.openai_api_key), settings.llm_model
    if settings.groq_api_key:
        return (
            OpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1"),
            settings.budget_llm_model,
        )
    return None


def analyze_transcript(transcript: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return {"summary","outcome","extracted"} or None if not possible."""
    if not transcript:
        return None
    pair = _llm_client()
    if pair is None:
        return None
    client, model = pair

    prompt = _PROMPT.format(outcomes=list(OUTCOMES), transcript=_transcript_text(transcript))
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        logger.warning(f"post-call analysis failed: {exc}")
        return None

    outcome = raw.get("outcome")
    if outcome not in OUTCOMES:
        outcome = "no_outcome"
    return {
        "summary": str(raw.get("summary") or "")[:2000],
        "outcome": outcome,
        "extracted": raw.get("extracted") or {},
    }
