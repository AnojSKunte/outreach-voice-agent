"""Outbound webhooks: notify external systems about call lifecycle events.

Events: ``call.started``, ``call.ended``, ``call.analyzed``,
``campaign.completed``. Each POST is JSON, signed with HMAC-SHA256 in the
``X-Outreach-Signature`` header when WEBHOOK_SECRET is set — receivers verify
the payload the same way they would a Stripe webhook.

Delivery is fire-and-forget with small retries; a failed webhook must never
affect a live call.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from outreach.config import get_settings


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def emit(event: str, data: dict[str, Any]) -> None:
    """POST ``event`` to every configured webhook URL (async, best-effort)."""
    settings = get_settings()
    urls = settings.webhook_url_list
    if not urls:
        return

    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    body = json.dumps(payload, default=str).encode()
    headers = {"Content-Type": "application/json", "X-Outreach-Event": event}
    if settings.webhook_secret:
        headers["X-Outreach-Signature"] = _sign(settings.webhook_secret, body)

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; skipping webhook delivery")
        return

    async with httpx.AsyncClient(timeout=10) as client:
        for url in urls:
            for attempt in (1, 2, 3):
                try:
                    resp = await client.post(url, content=body, headers=headers)
                    if resp.status_code < 400:
                        break
                    logger.warning(f"webhook {url} -> HTTP {resp.status_code} (attempt {attempt})")
                except Exception as exc:
                    logger.warning(f"webhook {url} failed (attempt {attempt}): {exc}")
                await asyncio.sleep(attempt * 2)


# Captured by the server at startup so sync code running in worker threads
# (e.g. call finalization) can still schedule webhook delivery.
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def emit_soon(event: str, data: dict[str, Any]) -> None:
    """Schedule ``emit`` without awaiting. Works from the event loop AND from
    worker threads (via the captured main loop); silently skipped if neither
    is available (e.g. plain scripts/tests)."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(emit(event, data))
        return
    except RuntimeError:
        pass
    if _main_loop is not None and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(emit(event, data), _main_loop)
