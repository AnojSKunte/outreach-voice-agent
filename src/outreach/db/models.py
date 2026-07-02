"""SQLAlchemy models for the CRM/calling core.

Design notes:
* Statuses are plain strings (checked in code, not DB enums) so adding one
  never needs a migration.
* Transcripts are stored as JSON on the call row — one row per call keeps
  queries trivial at this scale; move to a rows-per-utterance table if calls
  get very long.
* Everything has ``created_at`` for auditability.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ----------------------------------------------------------------------
# Leads
# ----------------------------------------------------------------------

LEAD_STATUSES = (
    "new",          # imported, never contacted
    "queued",       # selected by a campaign, waiting to be dialed
    "calling",      # a call is in flight right now
    "contacted",    # reached a human at least once
    "interested",   # positive outcome recorded
    "not_interested",
    "callback",     # asked to be called back later
    "unreachable",  # exhausted retries
    "dnc",          # asked not to be called / on the DNC list
    "converted",    # won
)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Arbitrary extra fields from CSV import / API ({"city": "...", ...});
    # exposed to the agent as call context.
    custom: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    # Number of call attempts made across campaigns.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Earliest time the next attempt may happen (retry backoff / callback).
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("campaigns.id"), nullable=True, index=True
    )
    campaign: Mapped["Campaign | None"] = relationship(back_populates="leads")
    calls: Mapped[list["Call"]] = relationship(back_populates="lead")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "phone": self.phone,
            "name": self.name,
            "company": self.company,
            "email": self.email,
            "notes": self.notes,
            "custom": self.custom or {},
            "status": self.status,
            "attempts": self.attempts,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "campaign_id": self.campaign_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ----------------------------------------------------------------------
# Campaigns
# ----------------------------------------------------------------------

CAMPAIGN_STATUSES = ("draft", "running", "paused", "completed")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    agent_id: Mapped[str] = mapped_column(String(100), index=True)
    # What the agent should achieve on each call (fed into the system prompt).
    goal: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)

    # Dialing policy
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=3)
    max_attempts_per_lead: Mapped[int] = mapped_column(Integer, default=3)
    retry_minutes: Mapped[int] = mapped_column(Integer, default=240)  # gap between attempts
    # Calling window, expressed in the campaign's timezone.
    calling_hours_start: Mapped[int] = mapped_column(Integer, default=10)  # 10:00
    calling_hours_end: Mapped[int] = mapped_column(Integer, default=19)    # 19:00
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Kolkata")
    # Days of week allowed, "0123456" (0=Monday). Default: Mon–Sat.
    calling_days: Mapped[str] = mapped_column(String(7), default="012345")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    leads: Mapped[list["Lead"]] = relationship(back_populates="campaign")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "agent_id": self.agent_id,
            "goal": self.goal,
            "status": self.status,
            "max_concurrent_calls": self.max_concurrent_calls,
            "max_attempts_per_lead": self.max_attempts_per_lead,
            "retry_minutes": self.retry_minutes,
            "calling_hours_start": self.calling_hours_start,
            "calling_hours_end": self.calling_hours_end,
            "timezone": self.timezone,
            "calling_days": self.calling_days,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ----------------------------------------------------------------------
# Calls
# ----------------------------------------------------------------------

CALL_STATUSES = (
    "initiated",   # outbound requested at the carrier
    "ringing",
    "in_progress", # media flowing, agent talking
    "completed",
    "voicemail",   # answering machine detected
    "no_answer",
    "busy",
    "failed",
    "canceled",
)


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # Carrier-side id (Twilio CallSid / Exotel Sid) once known.
    provider_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    agent_id: Mapped[str] = mapped_column(String(100), index=True)
    from_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    lead_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("leads.id"), nullable=True, index=True)
    lead: Mapped["Lead | None"] = relationship(back_populates="calls")
    campaign_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(20), default="initiated", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # [{"role": "user"|"assistant", "content": "...", "t": "iso"}...]
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    # Post-call analysis (LLM): summary, outcome tag, extracted fields.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)

    profile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def as_dict(self, include_transcript: bool = False) -> dict:
        d = {
            "id": self.id,
            "provider_call_id": self.provider_call_id,
            "direction": self.direction,
            "agent_id": self.agent_id,
            "from_number": self.from_number,
            "to_number": self.to_number,
            "lead_id": self.lead_id,
            "campaign_id": self.campaign_id,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "summary": self.summary,
            "outcome": self.outcome,
            "extracted": self.extracted or {},
            "profile": self.profile,
            "estimated_cost_usd": self.estimated_cost_usd,
            "recording_url": self.recording_url,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_transcript:
            d["transcript"] = self.transcript or []
        return d


# ----------------------------------------------------------------------
# Do-Not-Call list
# ----------------------------------------------------------------------


class DNCEntry(Base):
    __tablename__ = "dnc"

    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
