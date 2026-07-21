"""Offline checks against fixtures that mirror the real markup/feeds."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup  # noqa: E402

import notifier  # noqa: E402
from state import StockState  # noqa: E402
from stores import horii, marukyu  # noqa: E402
from stores.common import Item  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


# ---------------------------------------------------------------- Marukyu
CATALOG_HTML = """
<html><body>
<a href="/english/shop/products/catalog/matcha">Matcha</a>
<a href="/english/shop/products/1g36020c1" title="Kiwami Choan">Principal matcha Kiwami Choan 12,600</a>
<a href="/english/shop/products/1111020c1" title="Tenju">Principal matcha Tenju 20,100</a>
<a href="/english/shop/products/1141020c1" title="Unkaku">Principal matcha Unkaku 3,700</a>
<a href="/english/shop/cart">Cart</a>
</body></html>
"""

PRODUCT_IN_STOCK = """
<html><body><h1>Tenju</h1>
<p class="price">&yen;20,100</p>
<div class="spec"><span>SKU</span> 1111020C1 <span>Size</span> 20g can
  <p class="stock in-stock">In stock</p></div>
<div class="spec"><span>SKU</span> 1111040C1 <span>Size</span> 40g can
  <p class="stock in-stock">In stock</p></div>
</body></html>
"""

PRODUCT_MIXED = """
<html><body><h1>Unkaku</h1>
<p class="price">&yen;3,700</p>
<div class="spec"><span>SKU</span> 1141020C1 <span>Size</span> 20g can
  <p class="stock out-of-stock">This product is currently out of stock and unavailable.</p></div>
<div class="spec"><span>SKU</span> 1141040C1 <span>Size</span> 40g can
  <p class="stock in-stock">In stock</p></div>
</body></html>
"""

PRODUCT_PAGE_LEVEL_OOS = """
<html><body><h1>Yugen</h1>
<p class="price">&yen;2,000</p>
<p>Some Matcha products maybe temporarily marked as sold out.</p>
<p class="stock">This product is currently out of stock and unavailable.</p>
</body></html>
"""

PRODUCT_PAGE_LEVEL_OK = """
<html><body><h1>Kiwami Choan</h1>
<p class="price">&yen;12,600</p>
<p>Some Matcha products maybe temporarily marked as sold out.</p>
<p>SKU 1G36020C1 Size 20g can</p>
</body></html>
"""

VARIATIONS_JSON_PAGE = """
<html><body><h1>Wako</h1>
<form class="variations_form" data-product_variations='[
 {"sku":"1161020C1","is_in_stock":false,"display_price":2400,"attributes":{"attribute_size":"20g can"}},
 {"sku":"1161040C1","is_in_stock":true,"display_price":4720,"attributes":{"attribute_size":"40g can"}}]'>
</form></body></html>
"""


class FakeResponse:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload
        self.status_code = 200
        self.headers = {"content-type": "application/json" if payload is not None else "text/html"}

    def json(self):
        return self._payload


class FakeSession:
    """Serves canned responses keyed by substring of the requested URL."""

    def __init__(self, routes):
        self.routes = routes
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected URL requested: {url}")


print("\nMarukyu — product discovery")
session = FakeSession({"catalog/matcha": FakeResponse(CATALOG_HTML)})
products = marukyu.discover_products(session, marukyu.DEFAULT_CATALOGS, delay=0)
urls = [u for u, _ in products]
names = {n for _, n in products}
check("finds exactly the 3 product links", len(products) == 3, urls)
check("excludes catalog/cart links", all("/catalog/" not in u and "/cart" not in u for u in urls))
check("uses clean title attribute for names", "Kiwami Choan" in names, names)
check("builds absolute URLs", all(u.startswith("https://www.marukyu-koyamaen.co.jp") for u in urls))

print("\nMarukyu — stock detection")
session = FakeSession({"1111020c1": FakeResponse(PRODUCT_IN_STOCK)})
items = marukyu.scrape_product(session, "https://x/english/shop/products/1111020c1", "Tenju", 0)
check("splits two sizes into two items", len(items) == 2, [i.variant for i in items])
check("both sizes read as in stock", all(i.available for i in items))
check("per-variant detection used", all(i.detection == "dom-variant" for i in items))
check("keys are unique per SKU", len({i.key for i in items}) == 2)

session = FakeSession({"1141020c1": FakeResponse(PRODUCT_MIXED)})
items = marukyu.scrape_product(session, "https://x/english/shop/products/1141020c1", "Unkaku", 0)
by_sku = {i.key: i for i in items}
check("20g correctly sold out", by_sku["marukyu:1141020C1"].available is False)
check("40g correctly in stock", by_sku["marukyu:1141040C1"].available is True)

session = FakeSession({"1171020c1": FakeResponse(PRODUCT_PAGE_LEVEL_OOS)})
items = marukyu.scrape_product(session, "https://x/english/shop/products/1171020c1", "Yugen", 0)
check("page-level fallback flags sold out", items[0].available is False)
check("fallback labelled page-text", items[0].detection == "page-text")

session = FakeSession({"1g36020c1": FakeResponse(PRODUCT_PAGE_LEVEL_OK)})
items = marukyu.scrape_product(session, "https://x/english/shop/products/1g36020c1", "Kiwami Choan", 0)
check(
    "generic 'marked as sold out' banner does NOT false-positive",
    items[0].available is True,
)

session = FakeSession({"1161020c1": FakeResponse(VARIATIONS_JSON_PAGE)})
items = marukyu.scrape_product(session, "https://x/english/shop/products/1161020c1", "Wako", 0)
check("variations JSON preferred when present", all(i.detection == "variations-json" for i in items))
check("JSON stock flags respected", [i.available for i in items] == [False, True])
check("JSON variant labels read", items[0].variant == "20g can", items[0].variant)


# ------------------------------------------------------------------ Horii
FEED = {
    "products": [
        {
            "id": 111,
            "title": "Matcha Okunoyama",
            "handle": "matcha-okunoyama",
            "variants": [{"id": 1, "title": "Default Title", "price": "31.00", "available": False}],
        },
        {
            "id": 222,
            "title": "Sencha Meikun",
            "handle": "sencha-meikun",
            "variants": [
                {"id": 2, "title": "100g", "price": "18.00", "available": True},
                {"id": 3, "title": "200g", "price": "34.00", "available": False},
            ],
        },
    ]
}

print("\nHorii — Shopify feed parsing")
session = FakeSession({"products.json": FakeResponse(payload=FEED)})
items = horii.fetch(session, {"request_delay_seconds": 0})
check("flattens products into 3 variant rows", len(items) == 3, len(items))
check("single-variant product gets no size label", items[0].variant is None)
check("multi-variant product keeps size labels", items[1].variant == "100g")
check("availability read from feed", [i.available for i in items] == [False, True, False])
check(
    "URLs point at the English storefront",
    items[0].url == "https://horiishichimeien.com/en-sb/products/matcha-okunoyama",
    items[0].url,
)
check("prices formatted", items[1].price == "$18.00", items[1].price)

filtered = horii.fetch(session, {"request_delay_seconds": 0, "watchlist": ["matcha"]})
check("watchlist filters by name", len(filtered) == 1 and filtered[0].name == "Matcha Okunoyama")


# ------------------------------------------------------------------ State
print("\nState — restock transition logic")


def make(key, available, name="Thing"):
    return Item(key=key, store="Horii Shichimeien", name=name,
                url="https://example.com/p", available=available)


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "state.json")
    st = StockState(path)

    first = st.diff([make("a", True), make("b", False)])
    check("first run alerts on nothing (seeding)", first == [])

    st2 = StockState(path)
    check("state persists across restart", len(st2.data) == 2)

    second = st2.diff([make("a", True), make("b", True)])
    check("alerts only on false->true", [i.key for i in second] == ["b"], [i.key for i in second])

    third = st2.diff([make("a", True), make("b", True)])
    check("no repeat alert while still in stock", third == [])

    fourth = st2.diff([make("a", True), make("b", False)])
    check("going out of stock is silent", fourth == [])

    fifth = st2.diff([make("a", True), make("b", True)])
    check("re-restock alerts again", [i.key for i in fifth] == ["b"])

    new_item = st2.diff([make("c", True)])
    check("brand-new item does not alert on discovery", new_item == [])

    with open(path) as fh:
        saved = json.load(fh)
    check("state file is valid JSON with items", "items" in saved and len(saved["items"]) == 3)


# --------------------------------------------------------------- Notifier
print("\nNotifier — embed contents")
embed = notifier.build_embed(
    Item(key="k", store="Marukyu Koyamaen", name="Unkaku", variant="20g can",
         url="https://www.marukyu-koyamaen.co.jp/english/shop/products/1141020c1",
         price="¥3,700", available=True, detection="dom-variant")
)
check("title names the product", "Unkaku" in embed["title"])
check("embed links directly to the item", embed["url"].endswith("1141020c1"))
check("raw URL included in body", "1141020c1" in embed["description"])
check("store shown as a field", any(f["value"] == "Marukyu Koyamaen" for f in embed["fields"]))
check("size shown as a field", any(f["value"] == "20g can" for f in embed["fields"]))
check(
    "stores are colour-coded differently",
    notifier.STORE_COLORS["Marukyu Koyamaen"] != notifier.STORE_COLORS["Horii Shichimeien"],
)

batch = [make(str(i), True, f"Item {i}") for i in range(23)]
chunks = [batch[i : i + notifier.MAX_EMBEDS_PER_MESSAGE]
          for i in range(0, len(batch), notifier.MAX_EMBEDS_PER_MESSAGE)]
check("batches respect Discord's 10-embed limit", all(len(c) <= 10 for c in chunks) and len(chunks) == 3)

print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)
