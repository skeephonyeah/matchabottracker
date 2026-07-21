# Matcha Restock Watcher

Watches **Marukyu Koyamaen** and **Horii Shichimeien** and posts a Discord alert
the moment something goes from sold out to in stock. Each alert names the store,
the exact product and size, and links straight to that item's page.

## Deploying

Follow **DEPLOY_GITHUB.md** — complete step-by-step GitHub deployment, from
creating the repo to live alerts. No server needed.

## Setup (about 5 minutes)

**1. Get the files and install dependencies**

```bash
cd matcha-restock-bot
pip install -r requirements.txt
```

Python 3.9+.

**2. Create a Discord webhook**

In Discord: right-click your channel → **Edit Channel** → **Integrations** →
**Webhooks** → **New Webhook** → **Copy Webhook URL**.

This is why there's no bot token or server invite to deal with — a webhook can
post to one channel and nothing else, which is all a restock alert needs.

**3. Configure**

```bash
cp config.example.json config.json
```

Paste your webhook URL into `discord_webhook_url`. Alternatively keep it out of
the file entirely with `export DISCORD_WEBHOOK_URL="https://discord.com/api/..."`.

**4. Verify before you leave it running**

```bash
python bot.py --test
```

This prints current stock for both stores and sends nothing. Confirm the item
counts look right (Marukyu ~50 matcha products, Horii ~60 products) and check
the reported detection method — see "Verifying detection" below.

**5. Run it**

```bash
python bot.py
```

The first cycle records current stock and alerts on nothing — otherwise you'd
be pinged for everything that already happens to be in stock. Alerts begin on
the second cycle.

## How each store is checked

The two sites needed completely different approaches.

**Horii Shichimeien** runs on Shopify, so instead of scraping "Sold out" badges
out of the HTML, the bot reads Shopify's public products feed
(`/en-sb/products.json`). Availability comes straight from Shopify's inventory
as a boolean per variant, it covers the whole catalog in one request, and it
doesn't break when they restyle the theme.

**Marukyu Koyamaen** exposes no stock information on the catalog page at all —
prices only. Stock is visible only on individual product pages, where a sold-out
item shows *"This product is currently out of stock and unavailable."* So the bot
discovers products from the catalog, then checks each product page.

That means ~50 requests per Marukyu cycle, which is why the default interval is
5 minutes with a 1.5s pause between requests and a descriptive User-Agent. Please
put a real contact address in `user_agent`. If you want faster polling, narrow
the `watchlist` (below) rather than dropping the interval — fewer products
checked more often is both faster for you and lighter on their server.

Marukyu products come in multiple sizes (20g / 40g cans) that sell out
independently, so the bot tries three detection strategies in order:

1. `data-product_variations` JSON on the add-to-cart form — exact, per size.
2. Per-size DOM rows carrying their own stock class — per size.
3. Whole-page text match on the sold-out sentence — product-level only.

### Verifying detection

Every alert footer and the `--test` output names the strategy that produced the
reading. If Marukyu shows `page-text`, stock is being read for the product as a
whole rather than per size — you'll still be alerted, just without knowing which
can came back. Marukyu requires login to shop, so the richer per-size markup may
only appear when logged in.

To check what the real markup contains:

```bash
python bot.py --dump marukyu   # writes dumps/marukyu_product.html
python bot.py --dump horii     # writes dumps/horii_products.json
```

Open the dump, find how the sold-out state is marked next to each SKU, and the
selectors in `stores/marukyu.py` (`_variants_from_dom`) can be tightened to match.

## Watching only specific teas

An empty `watchlist` tracks everything. Add case-insensitive substrings to narrow it:

```json
"stores": {
  "marukyu": {
    "watchlist": ["Unkaku", "Wako", "Kinrin", "Aoarashi", "Isuzu"]
  },
  "horii": {
    "watchlist": ["Matcha"]
  }
}
```

This cuts Marukyu's request count sharply, so you can safely lower
`check_interval_seconds` to 120 or so.

## Configuration reference

| Key | Meaning |
|---|---|
| `discord_webhook_url` | Where alerts go. Env var `DISCORD_WEBHOOK_URL` wins if set. |
| `mention` | Prefix for alerts, e.g. `"@everyone"` or `"<@YOUR_USER_ID>"`. Empty = no ping. |
| `check_interval_seconds` | Seconds between cycles. Default 300. |
| `jitter_seconds` | Random ± offset so requests aren't perfectly periodic. |
| `state_file` | Where last-seen stock is stored. Delete it to re-seed. |
| `report_errors_to_discord` | Post a message when a store fails to scrape. |
| `stores.*.enabled` | Turn a store off without deleting its config. |
| `stores.*.request_delay_seconds` | Pause between requests to that store. |
| `stores.marukyu.catalog_urls` | Add more categories (green tea, seasonal, etc.). |

## Running it continuously

Cron, one cycle per run:

```
*/5 * * * * cd /path/to/matcha-restock-bot && /usr/bin/python3 bot.py --once >> bot.log 2>&1
```

systemd, always on:

```ini
[Unit]
Description=Matcha restock watcher
After=network-online.target

[Service]
WorkingDirectory=/path/to/matcha-restock-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=30
Environment=DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

[Install]
WantedBy=multi-user.target
```

Note it needs to run somewhere always-on to be useful — a laptop that sleeps
will miss restocks. A small VPS or a Raspberry Pi is plenty.

## Behaviour notes

- Alerts fire only on **sold out → in stock**. Going out of stock is silent, and
  an item staying in stock won't re-alert.
- Newly discovered products don't alert on first sight, only on a later restock.
- Alerts batch up to 10 items per message (Discord's embed limit) with rate-limit
  handling, so a full restock won't spam or drop messages.
- Failed cycles back off exponentially rather than hammering a struggling site.
- State survives restarts; delete `state.json` to reset.

## Reality check

Marukyu restocks sell out fast — sometimes within 30 minutes. A 5-minute poll
catches most of them, but a manual alert still means racing to checkout. Having
an account already created and logged in matters more than shaving the interval,
since Marukyu requires registration before you can order at all.

## Testing

```bash
python tests/test_offline.py
```

34 offline checks covering product discovery, all three Marukyu detection
strategies, Shopify feed parsing, restock-transition logic, and embed contents.
No network required.
