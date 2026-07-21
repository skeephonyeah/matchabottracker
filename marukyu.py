"""Marukyu Koyamaen (marukyu-koyamaen.co.jp) stock scraper.

The catalog page does NOT expose stock status -- it only lists products and
prices. Stock is only visible on each individual product page, where a
sold-out item renders WooCommerce's standard message:

    "This product is currently out of stock and unavailable."

Products often have multiple sizes (20g can / 40g can) that sell out
independently, so we try hardest to read per-variant status and only fall
back to whole-page status when the markup doesn't give us that.

Detection strategies, in order of preference:
  1. ``data-product_variations`` JSON on the variations form (per-variant,
     exact -- includes ``is_in_stock`` and ``sku``).
  2. Per-variant DOM rows carrying an ``out-of-stock`` / ``in-stock`` class.
  3. Whole-page text match on the sold-out sentence (product-level only).

Whichever fires is recorded on the Item as ``detection`` so you can see in
the logs how confident the reading is.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .common import Item, StoreError, matches_watchlist, polite_get

log = logging.getLogger(__name__)

STORE = "Marukyu Koyamaen"
BASE = "https://www.marukyu-koyamaen.co.jp"

# ?viewall=1 returns every product in the category on one page.
DEFAULT_CATALOGS = [
    f"{BASE}/english/shop/products/catalog/matcha?viewall=1",
]

OUT_OF_STOCK_TEXT = re.compile(r"out\s+of\s+stock\s+and\s+unavailable", re.I)
# Product URLs look like /english/shop/products/1g36020c1 -- an alphanumeric
# SKU-ish slug. Category pages live under .../products/catalog/... so we
# explicitly exclude those.
PRODUCT_PATH = re.compile(r"^/english/shop/products/(?!catalog/)([0-9a-z]{6,14})/?$", re.I)


def discover_products(session, catalogs: Iterable[str], delay: float) -> list[tuple[str, str]]:
    """Return [(product_url, name), ...] found across the catalog pages."""
    found: dict[str, str] = {}

    for catalog_url in catalogs:
        resp = polite_get(session, catalog_url, delay=delay)
        soup = BeautifulSoup(resp.text, "html.parser")

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            absolute = urljoin(BASE, href)
            path = absolute.replace(BASE, "").split("?")[0]
            if not PRODUCT_PATH.match(path):
                continue
            # The `title` attribute holds the clean product name; the link
            # text is polluted with category name and five currencies.
            name = (anchor.get("title") or anchor.get_text(" ", strip=True) or "").strip()
            if absolute not in found or (name and len(name) < len(found[absolute])):
                found[absolute] = name

    if not found:
        raise StoreError(
            "No product links found on the Marukyu catalog page -- the page "
            "layout may have changed. Run with --dump to inspect the HTML."
        )

    log.info("Marukyu: discovered %d products", len(found))
    return sorted(found.items())


def _variants_from_form_json(soup: BeautifulSoup) -> Optional[list[dict]]:
    """Strategy 1: WooCommerce embeds every variation as JSON on the form."""
    form = soup.find(attrs={"data-product_variations": True})
    if not form:
        return None
    raw = form.get("data-product_variations")
    if not raw or raw in ("false", "[]"):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, list) and data else None


def _variants_from_dom(soup: BeautifulSoup) -> Optional[list[dict]]:
    """Strategy 2: per-variant rows that carry their own stock class."""
    results: list[dict] = []

    # Look for containers that hold a SKU and also an in/out-of-stock marker.
    for node in soup.find_all(class_=re.compile(r"(variation|product-sku|item-sku|spec)", re.I)):
        text = node.get_text(" ", strip=True)
        sku_match = re.search(r"\b([0-9A-Z]{9,12})\b", text)
        if not sku_match:
            continue

        stock_node = node.find(class_=re.compile(r"(out-of-stock|in-stock|stock)", re.I))
        if stock_node is None:
            continue

        classes = " ".join(stock_node.get("class", []))
        stock_text = stock_node.get_text(" ", strip=True)
        out = "out-of-stock" in classes.lower() or bool(OUT_OF_STOCK_TEXT.search(stock_text))

        size_match = re.search(r"(\d+\s*g[^,\n]*)", text, re.I)
        results.append(
            {
                "sku": sku_match.group(1),
                "is_in_stock": not out,
                "variant": size_match.group(1).strip() if size_match else None,
            }
        )

    return results or None


def _price_for(soup: BeautifulSoup) -> Optional[str]:
    node = soup.find(class_=re.compile(r"price", re.I))
    if not node:
        return None
    text = node.get_text(" ", strip=True)
    match = re.search(r"[¥￥]\s?[\d,]+", text)
    return match.group(0) if match else (text[:40] or None)


def scrape_product(session, url: str, fallback_name: str, delay: float) -> list[Item]:
    resp = polite_get(session, url, delay=delay)
    soup = BeautifulSoup(resp.text, "html.parser")

    heading = soup.find("h1")
    name = fallback_name or (heading.get_text(strip=True) if heading else url.rsplit("/", 1)[-1])
    price = _price_for(soup)

    # --- Strategy 1: variations JSON -------------------------------------
    variations = _variants_from_form_json(soup)
    if variations:
        items = []
        for var in variations:
            sku = str(var.get("sku") or var.get("variation_id") or "")
            attrs = var.get("attributes") or {}
            variant = next((str(v) for v in attrs.values() if v), None) or sku
            items.append(
                Item(
                    key=f"marukyu:{sku or variant}",
                    store=STORE,
                    name=name,
                    variant=variant,
                    url=url,
                    price=(var.get("display_price") and f"¥{var['display_price']:,}") or price,
                    available=bool(var.get("is_in_stock")),
                    detection="variations-json",
                )
            )
        return items

    # --- Strategy 2: per-variant DOM rows --------------------------------
    dom_variants = _variants_from_dom(soup)
    if dom_variants:
        return [
            Item(
                key=f"marukyu:{var['sku']}",
                store=STORE,
                name=name,
                variant=var.get("variant") or var["sku"],
                url=url,
                price=price,
                available=bool(var["is_in_stock"]),
                detection="dom-variant",
            )
            for var in dom_variants
        ]

    # --- Strategy 3: whole-page fallback ---------------------------------
    page_text = soup.get_text(" ", strip=True)
    # The catalog notice mentions "sold out" generically on every matcha page,
    # so match only the specific WooCommerce out-of-stock sentence.
    out_of_stock = bool(OUT_OF_STOCK_TEXT.search(page_text))
    slug = url.rstrip("/").rsplit("/", 1)[-1]

    return [
        Item(
            key=f"marukyu:{slug}",
            store=STORE,
            name=name,
            variant=None,
            url=url,
            price=price,
            available=not out_of_stock,
            detection="page-text",
        )
    ]


def fetch(session, config: dict) -> list[Item]:
    """Entry point: return every tracked Marukyu item with current stock."""
    delay = float(config.get("request_delay_seconds", 1.5))
    catalogs = config.get("catalog_urls") or DEFAULT_CATALOGS
    watchlist = config.get("watchlist") or []

    products = discover_products(session, catalogs, delay)

    items: list[Item] = []
    for url, name in products:
        if not matches_watchlist(name or url, watchlist):
            continue
        try:
            items.extend(scrape_product(session, url, name, delay))
        except StoreError as exc:
            log.warning("Marukyu: skipping %s (%s)", url, exc)

    log.info("Marukyu: read stock for %d items", len(items))
    return items
