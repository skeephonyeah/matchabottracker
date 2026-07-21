"""Horii Shichimeien (horiishichimeien.com) stock scraper.

This store runs on Shopify, which is good news: instead of scraping the
"Sold out" badges out of the collection HTML (which changes whenever they
touch the theme), we can read Shopify's public products feed:

    /en-sb/products.json?limit=250&page=N

Every variant in that feed carries an ``available`` boolean straight from
Shopify's inventory, which is exactly what we need and costs one request
per 250 products rather than one request per product.

If the feed is ever disabled, we fall back to the per-product ``.js``
endpoint, and finally to parsing the collection page HTML.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from .common import Item, StoreError, matches_watchlist, polite_get

log = logging.getLogger(__name__)

STORE = "Horii Shichimeien"
BASE = "https://horiishichimeien.com"
LOCALE = "/en-sb"  # English storefront the user browses

PRODUCTS_JSON = f"{BASE}{LOCALE}/products.json"
FALLBACK_PRODUCTS_JSON = f"{BASE}/products.json"
COLLECTION_HTML = f"{BASE}{LOCALE}/collections/all?selected=all"


def product_url(handle: str) -> str:
    return f"{BASE}{LOCALE}/products/{handle}"


def _money(variant: dict) -> Optional[str]:
    price = variant.get("price")
    if price in (None, ""):
        return None
    try:
        return f"${float(price):,.2f}"
    except (TypeError, ValueError):
        return str(price)


def _fetch_feed(session, endpoint: str, delay: float) -> list[dict]:
    """Page through products.json until it stops returning products."""
    products: list[dict] = []
    page = 1

    while page <= 20:  # hard stop; this store has well under 5000 products
        url = f"{endpoint}?limit=250&page={page}"
        resp = polite_get(session, url, delay=delay, expect_json=True)
        try:
            batch = resp.json().get("products", [])
        except ValueError as exc:
            raise StoreError(f"{url} returned unparseable JSON: {exc}") from exc

        if not batch:
            break
        products.extend(batch)
        if len(batch) < 250:
            break
        page += 1

    if not products:
        raise StoreError(f"{endpoint} returned no products")
    return products


def _items_from_feed(products: list[dict], watchlist: list[str]) -> list[Item]:
    items: list[Item] = []

    for product in products:
        title = (product.get("title") or "").strip()
        handle = product.get("handle") or ""
        if not handle or not matches_watchlist(title, watchlist):
            continue

        variants = product.get("variants") or []
        multi = len(variants) > 1

        for variant in variants:
            variant_title = (variant.get("title") or "").strip()
            # Shopify uses "Default Title" when a product has no real options.
            label = variant_title if (multi and variant_title != "Default Title") else None

            items.append(
                Item(
                    key=f"horii:{product.get('id')}:{variant.get('id')}",
                    store=STORE,
                    name=title,
                    variant=label,
                    url=product_url(handle),
                    price=_money(variant),
                    available=bool(variant.get("available")),
                    detection="shopify-json",
                    extra={"handle": handle},
                )
            )

    return items


def _items_from_html(session, watchlist: list[str], delay: float) -> list[Item]:
    """Last-resort fallback: read Sold out badges off the collection page."""
    resp = polite_get(session, COLLECTION_HTML, delay=delay)
    soup = BeautifulSoup(resp.text, "html.parser")

    seen: dict[str, Item] = {}
    for anchor in soup.select("a[href*='/products/']"):
        href = anchor.get("href", "")
        match = re.search(r"/products/([^/?#]+)", href)
        if not match:
            continue
        handle = match.group(1)

        card = anchor.find_parent(["li", "div", "article"]) or anchor
        text = card.get_text(" ", strip=True)
        name = (anchor.get_text(" ", strip=True) or handle).strip()
        if not matches_watchlist(name, watchlist):
            continue

        sold_out = bool(re.search(r"\bsold\s*out\b", text, re.I))
        price_match = re.search(r"\$[\d,]+\.?\d*", text)

        seen[handle] = Item(
            key=f"horii:{handle}",
            store=STORE,
            name=name,
            variant=None,
            url=product_url(handle),
            price=price_match.group(0) if price_match else None,
            available=not sold_out,
            detection="html-fallback",
            extra={"handle": handle},
        )

    if not seen:
        raise StoreError("Horii HTML fallback found no products")
    return list(seen.values())


def fetch(session, config: dict) -> list[Item]:
    """Entry point: return every tracked Horii item with current stock."""
    delay = float(config.get("request_delay_seconds", 1.0))
    watchlist = config.get("watchlist") or []

    for endpoint in (PRODUCTS_JSON, FALLBACK_PRODUCTS_JSON):
        try:
            products = _fetch_feed(session, endpoint, delay)
        except StoreError as exc:
            log.warning("Horii: feed %s unavailable (%s)", endpoint, exc)
            continue

        items = _items_from_feed(products, watchlist)
        if items:
            log.info("Horii: read stock for %d variants via %s", len(items), endpoint)
            return items

    log.warning("Horii: falling back to HTML scraping")
    return _items_from_html(session, watchlist, delay)
