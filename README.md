# Outreach — AI Calling Agent Platform

AI voice agents that handle real phone calls for client businesses: inbound
reception, outbound lead generation, and bulk calling campaigns — with lead
management, transcripts, post-call analysis, a REST API, webhooks, and a web
dashboard. Multi-tenant by design: each client is a YAML config folder, not
code.

Feature parity targets are the 2026 market leaders (Vapi, Retell, Bland,
Synthflow): sub-second cascaded pipeline with barge-in, answering-machine
detection, retry policies, calling-hour windows, DNC list, AI disclosure,
call transfer, campaign analytics, and per-call cost tracking.

## Architecture

A **cascaded** voice pipeline — separate speech-to-text, LLM, and
text-to-speech — orchestrated in Python with **Pipecat**, fronted by a phone
carrier over a bidirectional media WebSocket, deployed as a single web
service. The campaign engine (bulk outbound dialer) runs inside the same
process.

```
                       inbound: caller dials the client's number
                       outbound: campaign engine / API dials the lead
                                        │
             Twilio (dev + international) or Exotel (India production)
                                        │  media stream (WS)
                             ┌──────────┴──────────┐
                             │  outreach.server    │  FastAPI
                             │  /twiml /ws /api/v1 │  + dashboard at /
                             └──────────┬──────────┘
                    ┌───────────────────┼──────────────────────┐
                    │ Pipecat pipeline (assembled per call)     │
                    │   STT → LLM (+tools) → TTS   + transcript │
                    │   profile: premium | budget               │
                    └───────────────────┬──────────────────────┘
                                        │
              SQLite/Postgres: leads, campaigns, calls, DNC
              post-call: summary + outcome (LLM) → lead status → webhooks
```

### Provider profiles (cost vs quality — per agent, one line of YAML)

| | premium | budget (India-first) |
|---|---|---|
| STT | Deepgram Nova-3 (Hindi code-switch) | Sarvam Saarika (Hindi/Hinglish) |
| LLM | OpenAI gpt-4o-mini (configurable) | Groq Llama 3.3 / Gemini Flash-Lite |
| TTS | Cartesia (or ElevenLabs) | Sarvam Bulbul |
| AI cost | ~$0.05–0.09 / min | ~₹1–2 / min (~$0.015–0.025) |

Set `profile: budget` in an agent's config, or platform-wide with
`DEFAULT_PROFILE`. Individual models remain overridable per agent.

## Layout

```
agents/<client>/config.yaml   # a client's agent (persona, knowledge, actions,
agents/<client>/knowledge.md  #   outbound goal, voicemail policy, compliance)
src/outreach/config.py        # process settings from env vars
src/outreach/agents/          # AgentConfig schema + number->agent registry
src/outreach/actions/         # tools the LLM may call (swap mocks for real APIs)
src/outreach/providers/       # premium/budget profile -> STT/LLM/TTS services
src/outreach/pipeline/        # assembles the Pipecat pipeline per call
src/outreach/telephony/       # Twilio + Exotel carriers (dial-out, AMD, transfer)
src/outreach/db/              # SQLAlchemy models: leads, campaigns, calls, DNC
src/outreach/campaigns.py     # bulk-dial engine: windows, retries, concurrency
src/outreach/calls.py         # call lifecycle: status, finalize, lead outcomes
src/outreach/analysis.py      # post-call summary/outcome extraction (LLM)
src/outreach/api.py           # REST API v1 (X-API-Key)
src/outreach/events.py        # signed outbound webhooks
src/outreach/server.py        # FastAPI app: webhooks, media WS, API, dashboard
src/outreach/dashboard/       # single-file web dashboard
scripts/chat_with_agent.py    # talk to any agent in text — no keys needed
```

## Quick start (no API keys needed)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

PYTHONPATH=src python scripts/validate_agents.py    # check agent configs
PYTHONPATH=src pytest -q                            # run the test suite
PYTHONPATH=src python scripts/chat_with_agent.py seaside-hotel   # text chat
PYTHONPATH=src uvicorn outreach.server:app --port 8000           # dashboard at :8000
```

With any single LLM key (free Groq key works) the text chat becomes a real
conversation with tools. See **docs/SETUP.md** for the full path to live
phone calls, campaign setup, and going to production in India.

## API in 30 seconds

```bash
H='-H "X-API-Key: $OUTREACH_API_KEY" -H "Content-Type: application/json"'

# import leads, create a campaign, start it
curl -X POST :8000/api/v1/leads/import -F file=@leads.csv
curl -X POST :8000/api/v1/campaigns $H -d '{"name":"July","agent_id":"lead-gen-demo","goal":"qualify solar interest"}'
curl -X POST :8000/api/v1/campaigns/<id>/start $H

# or one call right now
curl -X POST :8000/api/v1/calls $H -d '{"phone":"+91XXXXXXXXXX"}'
```

Webhooks (`call.started|ended|analyzed`, `campaign.completed`) POST to
`WEBHOOK_URLS`, HMAC-signed with `WEBHOOK_SECRET` — that's the generic seam
for CRM integration; point it at Zapier/Make/n8n or your own endpoint.

## Compliance is built in, not bolted on

DNC list enforced before every dial (auto-added when a callee asks), attempt
caps, per-campaign calling windows in local time, AI disclosure line per
agent. For India production use Exotel (`TELEPHONY_PROVIDER=exotel`): Twilio
cannot issue Indian caller IDs and TRAI treats non-consented calls as UCC.
Only call leads who have opted in.

## Status

- [x] Milestone 1 — foundation: config, agent schema, registries, tests.
- [x] Milestone 2 — Pipecat cascaded pipeline from config (+ profiles, transcripts).
- [x] Milestone 3 — telephony: inbound + outbound + AMD + transfer, media WS server.
- [x] Milestone 4 — product: leads/campaigns/DNC, bulk dialer, post-call analysis,
  REST API, webhooks, dashboard.
- [ ] Milestone 5 — first live call & production hardening (needs keys; see docs/SETUP.md).
