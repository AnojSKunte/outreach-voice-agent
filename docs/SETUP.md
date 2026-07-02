# Setup guide — from zero to live calls

Work through the stages in order; each one is independently verifiable.
Everything before Stage 3 is free.

## Stage 0 — local, no keys (5 min)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
PYTHONPATH=src python scripts/validate_agents.py
PYTHONPATH=src pytest -q
PYTHONPATH=src python scripts/smoke_imports.py      # verifies pipecat symbols
PYTHONPATH=src uvicorn outreach.server:app --port 8000
```

Open http://localhost:8000 — the dashboard works fully (leads, campaigns,
DNC) with no keys. Calls obviously can't connect yet.

## Stage 1 — text conversations (free, ~5 min)

Get a **free Groq key** (console.groq.com — no card) and put it in `.env` as
`GROQ_API_KEY`. Then:

```bash
PYTHONPATH=src python scripts/chat_with_agent.py seaside-hotel
PYTHONPATH=src python scripts/chat_with_agent.py lead-gen-demo --outbound
```

You are now talking to the exact persona/knowledge/tools a phone caller
would get. Iterate on the YAML configs here — it's the cheapest place to
tune prompts.

## Stage 2 — voice providers (free credits)

Pick a profile (you can run both; agents choose per-config):

| Profile | Sign up | Free credit |
|---|---|---|
| budget | dashboard.sarvam.ai → `SARVAM_API_KEY` | ₹1,000 |
| budget LLM | console.groq.com → `GROQ_API_KEY` | free tier |
| premium | console.deepgram.com → `DEEPGRAM_API_KEY` | $200 |
| premium | play.cartesia.ai → `CARTESIA_API_KEY` | free tier |
| premium LLM | platform.openai.com → `OPENAI_API_KEY` | pay-as-you-go |

## Stage 3 — first phone call (Twilio, ~$1)

1. Create a Twilio account, buy a number (or use the trial number), set
   `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.
2. Expose your local server:
   ```bash
   ngrok http 8000        # note the hostname, e.g. abc123.ngrok-free.app
   ```
   Set `PUBLIC_HOST=abc123.ngrok-free.app` in `.env` and restart the server.
3. **Inbound**: in the Twilio console, set the number's Voice webhook to
   `https://<PUBLIC_HOST>/twiml` (HTTP POST). Call the number — the
   `DEFAULT_AGENT_ID` agent answers.
4. **Outbound**: from the dashboard Leads tab, add your own number as a lead
   and press **Call now** (or `POST /api/v1/calls`). Twilio trial accounts
   can only call verified numbers.

## Stage 4 — campaigns

1. Dashboard → Campaigns → **New campaign** (agent, goal, calling hours,
   concurrency, retries).
2. Leads → **Import CSV** with the campaign selected. Columns: `phone`
   (E.164, required), `name`, `company`, `email`, `notes` — any extra
   columns become custom fields the agent can see and use on the call.
3. Campaign → **Start**. The engine dials within the calling window,
   respects the DNC list, retries no-answers, and marks outcomes from
   post-call analysis (interested / callback / not interested / DNC...).

## Stage 5 — deploy (Render)

The repo ships a `render.yaml` blueprint: push to GitHub, create a new
Blueprint service on render.com, fill the `sync: false` secrets when
prompted. `PUBLIC_HOST` and `OUTREACH_API_KEY` are set automatically.
Update the Twilio webhook to `https://<your-app>.onrender.com/twiml`.

Any host that runs a persistent Python process with WebSockets works the
same way (Railway, Fly.io, a VPS): `uvicorn outreach.server:app`.

## Stage 6 — production in India (Exotel)

Twilio cannot issue Indian local numbers; international caller IDs hurt
pickup and TRAI treats unsolicited commercial calls as UCC. For Indian
clients:

1. Get an Exotel account + ExoPhone (Indian number). KYC required.
2. In Exotel's flow builder, create a **Voicebot** applet pointing to
   `wss://<PUBLIC_HOST>/ws` and attach it to the ExoPhone (inbound).
3. Set in env: `TELEPHONY_PROVIDER=exotel`, `EXOTEL_SID`, `EXOTEL_API_KEY`,
   `EXOTEL_API_TOKEN`, `EXOTEL_FROM_NUMBER`.
4. Only call opted-in leads; keep `disclose_ai: true`; respect the DNC tab.

Pipecat speaks Exotel's stream protocol natively (`ExotelFrameSerializer`),
so the voice pipeline is identical — only the carrier changes.

## Adding a client (no code)

```bash
cp -r agents/lead-gen-demo agents/acme-clinic
# edit agents/acme-clinic/config.yaml + knowledge.md
PYTHONPATH=src python scripts/validate_agents.py
# restart the server (configs load at boot)
```

Wire real actions (booking systems, CRMs) by adding functions in
`src/outreach/actions/builtin.py` — the `@register` decorator exposes them
to any agent that lists the action name in `allowed_actions`.

## Troubleshooting

* `scripts/smoke_imports.py` fails → the installed `pipecat-ai` version
  moved a symbol; pin the version that passes or adjust the import path.
* Inbound call connects then silence → check `PUBLIC_HOST` has no scheme
  and the server logs show "media stream open".
* Outbound says "failed" instantly → Twilio trial restrictions (verify the
  destination number) or missing `TWILIO_FROM_NUMBER`.
* Budget profile errors → `SARVAM_API_KEY` missing, or install extras:
  `pip install "pipecat-ai[sarvam]"`.
