"""Discord delivery: one embed per restocked item, batched into messages."""

from __future__ import annotations

import logging
import time
from typing import Iterable

import requests

from stores.common import Item

log = logging.getLogger(__name__)

# Distinct colors so you can tell the two stores apart at a glance.
STORE_COLORS = {
    "Marukyu Koyamaen": 0x2E7D32,  # deep green
    "Horii Shichimeien": 0x8D6E63,  # earthy brown
}
DEFAULT_COLOR = 0x4CAF50

MAX_EMBEDS_PER_MESSAGE = 10


def build_embed(item: Item) -> dict:
    fields = [{"name": "Store", "value": item.store, "inline": True}]

    if item.variant:
        fields.append({"name": "Size / Option", "value": item.variant, "inline": True})
    if item.price:
        fields.append({"name": "Price", "value": item.price, "inline": True})

    return {
        "title": f"🍵 Back in stock: {item.name}",
        "url": item.url,
        "description": f"**[Open the product page]({item.url})**\n`{item.url}`",
        "color": STORE_COLORS.get(item.store, DEFAULT_COLOR),
        "fields": fields,
        "footer": {"text": f"{item.store} · detected via {item.detection}"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    }


def _post(webhook_url: str, payload: dict, retries: int = 4) -> bool:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=20)
        except requests.RequestException as exc:
            log.warning("Discord POST failed (%d/%d): %s", attempt, retries, exc)
            time.sleep(2**attempt)
            continue

        if resp.status_code in (200, 204):
            return True

        if resp.status_code == 429:
            # Discord tells us exactly how long to wait.
            try:
                wait = float(resp.json().get("retry_after", 5))
            except ValueError:
                wait = 5.0
            log.warning("Discord rate limited, sleeping %.1fs", wait)
            time.sleep(wait + 0.5)
            continue

        log.error("Discord POST -> HTTP %d: %s", resp.status_code, resp.text[:300])
        time.sleep(2**attempt)

    return False


def notify_restocks(webhook_url: str, items: Iterable[Item], mention: str = "") -> bool:
    """Send a message (or several) announcing the given restocked items."""
    items = list(items)
    if not items:
        return True

    # Group by store so a mixed batch still reads cleanly.
    items.sort(key=lambda i: (i.store, i.name, i.variant or ""))
    ok = True

    for start in range(0, len(items), MAX_EMBEDS_PER_MESSAGE):
        chunk = items[start : start + MAX_EMBEDS_PER_MESSAGE]
        stores = sorted({i.store for i in chunk})

        content = f"**Restock alert — {', '.join(stores)}** ({len(chunk)} item(s))"
        if mention:
            content = f"{mention} {content}"

        payload = {
            "content": content,
            "embeds": [build_embed(i) for i in chunk],
            "allowed_mentions": {"parse": ["everyone", "roles", "users"]}
            if mention
            else {"parse": []},
        }

        if not _post(webhook_url, payload):
            ok = False
        time.sleep(1)  # stay comfortably under Discord's webhook rate limit

    return ok


def notify_text(webhook_url: str, message: str) -> bool:
    """Plain message, used for startup pings and error reports."""
    return _post(webhook_url, {"content": message[:1900], "allowed_mentions": {"parse": []}})
