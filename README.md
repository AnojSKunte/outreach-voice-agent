# Outreach — Voice AI Calling Agent Platform

AI voice agents that handle real phone calls for client businesses. Each
client gets a phone number and an agent configured by a structured context
file (persona, knowledge base, permitted actions). Built to be multi-tenant,
though phase 1 proves a single inbound call end-to-end.

## Architecture (the short version)

A **cascaded** voice pipeline — separate speech-to-text, LLM, and
text-to-speech — orchestrated in Python with **Pipecat**, fronted by a
**Twilio** phone number over a bidirectional Media Streams WebSocket, deployed
as a single persistent **Render** web service.

```
caller ──PSTN──► Twilio number ──Media Streams (WS)──► Render web service
                                                          │
                                   ┌──────────────────────┴───────────────────────┐
                                   │  Pipecat pipeline (assembled per call)         │
                                   │  Deepgram STT → OpenAI LLM (+tools) → Cartesia │
                                   └──────────────────────┬───────────────────────┘
                                                          │
                              agent config (persona, knowledge, actions)
                              resolved from the dialed number
```

Cascaded over a single speech-to-speech model because it keeps providers
swappable, gives clean transcripts and tool-calling control, and has
predictable per-minute cost — while still landing near the ~550–800 ms
end-to-end latency that makes a call feel natural.

## Layout

```
agents/<client>/config.yaml   # a client's context file (data, not code)
agents/<client>/knowledge.md  # that client's FAQ/policies/pricing
src/outreach/config.py        # process settings from env vars
src/outreach/agents/          # AgentConfig schema + number->agent registry
src/outreach/actions/         # tools the LLM may call (mock-backed in phase 1)
src/outreach/pipeline/        # assembles the pipeline from a config  (milestone 2)
src/outreach/server.py        # telephony webhook + media-stream WS  (milestone 3)
render.yaml                   # persistent web service deployment
```

Adding a client is adding an `agents/<client>/` folder — no code changes.

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill in keys (never commit .env)

# validate every agent config:
PYTHONPATH=src python scripts/validate_agents.py

# run the foundation tests:
PYTHONPATH=src pytest -q
```

## Secrets

No credentials live in the repo. Local dev reads a gitignored `.env`;
production reads Render environment variables. `.env.example` lists the names
with empty values.

## Status

- [x] Milestone 1 — project foundation: config, agent schema, number→agent
  registry, action registry, sample client, tests.
- [ ] Milestone 2 — Pipecat cascaded pipeline assembled from a config.
- [ ] Milestone 3 — Twilio telephony + FastAPI WebSocket server.
- [ ] Milestone 4 — deploy to Render + first live inbound call.
