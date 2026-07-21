"""Discord delivery: every restock in ONE message, ping on a single line."""

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


# Discord's hard limits. Exceeding any of these makes the API reject the
# whole message, so the builder degrades gracefully instead of failing.
CONTENT_LIMIT = 2000        # the visible message text
DESCRIPTION_LIMIT = 4096    # embed description
FIELD_VALUE_LIMIT = 1024    # a single embed field
TOTAL_EMBED_LIMIT = 6000    # everything in one embed, combined
MAX_FIELDS = 25


def _item_line(item: Item, compact: bool) -> str:
    """One bullet for an item. Non-compact also prints the bare URL."""
    price = f" · {item.price}" if item.price else ""
    headline = f"• **[{item.label}]({item.url})**{price}"
    return headline if compact else f"{headline}\n{item.url}"


def _fields_for(items: list[Item], compact: bool) -> list[dict]:
    """Group items under a heading per store, splitting fields at 1024 chars."""
    fields: list[dict] = []

    for store in sorted({i.store for i in items}):
        lines = [
            _item_line(i, compact)
            for i in sorted(items, key=lambda x: (x.name, x.variant or ""))
            if i.store == store
        ]

        chunk: list[str] = []
        length = 0
        part = 0
        for line in lines:
            if length + len(line) + 1 > FIELD_VALUE_LIMIT and chunk:
                part += 1
                fields.append({
                    "name": store if part == 1 else f"{store} (cont.)",
                    "value": "\n".join(chunk),
                    "inline": False,
                })
                chunk, length = [], 0
            chunk.append(line)
            length += len(line) + 1

        if chunk:
            part += 1
            fields.append({
                "name": store if part == 1 else f"{store} (cont.)",
                "value": "\n".join(chunk),
                "inline": False,
            })

    return fields


def _embed_size(embed: dict) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    for field in embed.get("fields", []):
        total += len(field["name"]) + len(field["value"])
    total += len(embed.get("footer", {}).get("text", ""))
    return total


def build_restock_payload(items: list[Item], mention: str = "") -> dict:
    """Every restocked item in a single message.

    The ping sits on one line at the top; the items live in one embed below it.
    If the batch is too large for Discord's limits the format degrades - first
    dropping the bare URLs (the masked links still work), then truncating - so
    a huge restock still delivers rather than being rejected wholesale.
    """
    items = sorted(items, key=lambda i: (i.store, i.name, i.variant or ""))
    counts = {
        store: sum(1 for i in items if i.store == store)
        for store in sorted({i.store for i in items})
    }
    breakdown = ", ".join(f"{n} from {store}" for store, n in counts.items())

    # One line, no newlines - the ping and the summary together.
    noun = "item" if len(items) == 1 else "items"
    content = f"🍵 **{len(items)} {noun} back in stock** — {breakdown}"
    if mention:
        content = f"{mention} {content}"
    content = content.replace("\n", " ")[:CONTENT_LIMIT]

    colour = (
        STORE_COLORS.get(items[0].store, DEFAULT_COLOR)
        if len(counts) == 1
        else DEFAULT_COLOR
    )

    def assemble(compact: bool, limit: int | None = None) -> dict:
        subset = items[:limit] if limit else items
        embed = {
            "title": "Back in stock",
            "color": colour,
            "fields": _fields_for(subset, compact)[:MAX_FIELDS],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        }
        if limit and limit < len(items):
            embed["description"] = f"_Showing {limit} of {len(items)} — see the shops for the rest._"
        return embed

    # Full detail first; fall back only as far as needed.
    embed = assemble(compact=False)
    if _embed_size(embed) > TOTAL_EMBED_LIMIT or len(embed["fields"]) > MAX_FIELDS:
        embed = assemble(compact=True)

    if _embed_size(embed) > TOTAL_EMBED_LIMIT or len(embed["fields"]) > MAX_FIELDS:
        low, high = 1, len(items)
        while low < high:  # largest number of items that still fits
            mid = (low + high + 1) // 2
            candidate = assemble(compact=True, limit=mid)
            if _embed_size(candidate) <= TOTAL_EMBED_LIMIT and len(candidate["fields"]) <= MAX_FIELDS:
                low = mid
            else:
                high = mid - 1
        embed = assemble(compact=True, limit=low)

    return {
        "content": content,
        "embeds": [embed],
        "allowed_mentions": {"parse": ["everyone", "roles", "users"]} if mention else {"parse": []},
    }


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
    """Post every restocked item as ONE message."""
    items = list(items)
    if not items:
        return True

    payload = build_restock_payload(items, mention)
    ok = _post(webhook_url, payload)
    if not ok:
        log.error("Failed to deliver restock alert for %d item(s)", len(items))
    return ok


def notify_text(webhook_url: str, message: str) -> bool:
    """Plain message, used for startup pings and error reports."""
    return _post(webhook_url, {"content": message[:1900], "allowed_mentions": {"parse": []}})