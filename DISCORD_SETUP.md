# Setting this up in Discord — step by step

There are two ways to get alerts into Discord. Pick one.

| | **Webhook** (`bot.py`) | **Bot** (`discord_bot.py`) |
|---|---|---|
| Setup time | ~2 min | ~10 min |
| Needs a Discord application | No | Yes |
| Needs server admin rights | No (just channel access) | Yes, to invite it |
| Posts restock alerts | Yes | Yes |
| Slash commands (`/status`, `/check`…) | No | Yes |
| Shows as an online member | No | Yes |

If you just want alerts, the webhook is genuinely fine and there's no shame in
it — most restock bots are webhooks. Everything below covers the **bot** route
since that's what you asked for; the webhook route is at the bottom.

---

## Which files you need

All of these, keeping the folder structure exactly as-is:

```
matcha-restock-bot/
├── discord_bot.py          ← run THIS for the slash-command bot
├── bot.py                  ← webhook version + the --test/--dump tools
├── notifier.py             ← builds the alert embeds
├── state.py                ← remembers what was in stock last check
├── config.json             ← YOU CREATE THIS (copy config.example.json)
├── config.example.json
├── requirements.txt
├── stores/
│   ├── __init__.py         ← must exist, even though it's empty
│   ├── common.py
│   ├── marukyu.py
│   └── horii.py
└── tests/
    └── test_offline.py
```

`discord_bot.py` imports from `stores/`, `state.py`, and `notifier.py`, so you
can't just take that one file on its own. Keep `bot.py` even if you're running
the bot version — it holds the `--test` and `--dump` tools you'll want for
troubleshooting.

You need **Python 3.9 or newer**. Check with `python3 --version`.

---

## Step 1 — Install the dependencies

```bash
cd matcha-restock-bot
pip install -r requirements.txt
```

Verify it worked:

```bash
python3 -c "import discord, requests, bs4; print('ok', discord.__version__)"
```

You want version **2.3 or higher**. If you get `1.7.x`, you've hit the old
abandoned `discord.py`; fix it with:

```bash
pip uninstall -y discord discord.py && pip install -U "discord.py>=2.3"
```

A `PyNaCl is not installed` warning is harmless — that's for voice chat.

---

## Step 2 — Test scraping BEFORE touching Discord

Do this first. If scraping is broken, no amount of Discord setup will help, and
you'll waste time debugging the wrong layer.

```bash
cp config.example.json config.json
python3 bot.py --test
```

This sends nothing anywhere. It prints current stock for both stores and exits.
You should see something like:

```
=== Marukyu Koyamaen — 3/50 in stock ===
❌ sold out  Unkaku — 20g can ¥3,700
✅ IN STOCK  Aoarashi — 40g can ¥2,120
             https://www.marukyu-koyamaen.co.jp/english/shop/products/11a1040c1
    (detection method: dom-variant)

=== Horii Shichimeien — 0/62 in stock ===
...
    (detection method: shopify-json)
```

**What to check:**

- Roughly **50** Marukyu items and **60** Horii items. Far fewer means product
  discovery is failing.
- Horii should say `shopify-json`. If it says `html-fallback`, the Shopify feed
  is blocked — still works, less reliable.
- Marukyu will say `dom-variant`, `variations-json`, or `page-text`. The first
  two track each can size separately; `page-text` only tracks the product as a
  whole. See "Marukyu detection" at the bottom.

If you get zero items or errors, run `python3 bot.py --dump marukyu` and
`python3 bot.py --dump horii`, then check the files written to `dumps/`.

You can also run the offline test suite, which needs no internet at all:

```bash
python3 tests/test_offline.py    # 34 checks, all should PASS
```

---

## Step 3 — Create the Discord application

1. Go to **https://discord.com/developers/applications**
2. Click **New Application** (top right).
3. Name it something like `Matcha Watcher`. Accept the terms → **Create**.

---

## Step 4 — Get the bot token

1. In the left sidebar, click **Bot**.
2. Click **Reset Token** → **Yes, do it!** (confirm with 2FA if prompted).
3. Click **Copy**. This is your token.

**This token is a password for your bot.** Anyone who has it controls it. Never
paste it into a screenshot, a GitHub repo, or a chat message. If it leaks, come
back here and hit Reset Token — the old one dies instantly.

While you're on this page, scroll to **Privileged Gateway Intents**. Leave all
three **OFF**. Slash commands don't need them, and enabling Message Content
without needing it just makes verification harder later.

---

## Step 5 — Invite the bot to your server

1. Left sidebar → **OAuth2** → **URL Generator**.
2. Under **Scopes**, tick exactly two boxes:
   - ✅ `bot`
   - ✅ `applications.commands`
3. A **Bot Permissions** panel appears below. Tick:
   - ✅ Send Messages
   - ✅ Embed Links
   - ✅ Read Message History
4. Copy the generated URL at the bottom, open it in your browser, pick your
   server, and click **Authorize**.

If you forget `applications.commands`, the bot will join fine but the slash
commands will never appear. That's the single most common setup mistake.

The bot will show as **offline** in your member list until Step 7. That's normal.

---

## Step 6 — Get your channel and server IDs

First enable Developer Mode: **User Settings** (gear, bottom-left) → **Advanced**
→ turn on **Developer Mode**.

Then:

- **Channel ID** — right-click the channel you want alerts in → **Copy Channel ID**
- **Server ID** — right-click your server's icon → **Copy Server ID**

Now open `config.json` and fill in the three values:

```json
{
  "discord_bot_token": "MTIzNDU2Nzg5...your token...",
  "discord_channel_id": 1122334455667788990,
  "discord_guild_id": 9988776655443322110,
  ...
}
```

The two IDs are **numbers with no quotes**. The token **is** in quotes.

`discord_guild_id` is optional but worth setting: with it, slash commands appear
in your server instantly. Without it, they're registered globally and can take
up to an hour to show up.

### Keeping the token out of the file (optional, recommended)

If this folder might ever end up in git or a shared drive, leave
`discord_bot_token` as the placeholder and pass it in as an environment
variable instead — that takes priority over the file:

```bash
export DISCORD_BOT_TOKEN="MTIzNDU2Nzg5..."
export DISCORD_CHANNEL_ID="1122334455667788990"
python3 discord_bot.py
```

The bot also won't write the token back into `config.json` when you edit the
watchlist via slash commands, if it came from the environment.

---

## Step 7 — Start the bot

```bash
python3 discord_bot.py
```

Expected output:

```
02:17:35 INFO  state: No state file at state.json -- first run will seed, not alert
02:17:36 INFO  discord_bot: Slash commands synced to guild 998877...
02:17:37 INFO  discord_bot: Logged in as Matcha Watcher#1234 (id 1122...)
02:19:02 INFO  discord_bot: Cycle done — 0 restock(s)
```

In Discord you should now see the bot come **online** with a "Watching matcha
stock" status, and a message in your channel saying it's recording current stock.

**The first cycle intentionally alerts on nothing.** It records what's in stock
right now as the baseline — otherwise you'd get pinged for every item that
already happens to be available, which isn't a restock. Alerts start from the
second cycle onward.

The first cycle takes a while (~90 seconds), because Marukyu needs one request
per product page with a polite pause between each.

---

## Step 8 — Test it end to end

Type `/` in your channel. You should see six commands:

| Command | What it does |
|---|---|
| `/status` | Last check, next check, items tracked, any errors |
| `/stock` | Everything in stock right now, with links |
| `/check` | Forces a check immediately, without waiting |
| `/watch` | Only alert on teas matching a name |
| `/unwatch` | Remove a watchlist term |
| `/watchlist` | Show current watchlists |

Run **`/status`** first — it should report a recent check and a nonzero item
count. Replies are ephemeral (only you see them), so you can spam them without
cluttering the channel.

Then run **`/check`** to force a cycle. It'll think for 60–120 seconds, then
report how many items it checked.

### Proving alerts actually fire

`/check` won't show you an alert if nothing genuinely restocked. To test the
alert path itself, fake a restock by editing the saved state:

```bash
# stop the bot first (Ctrl-C)
python3 - <<'PY'
import json
s = json.load(open("state.json"))
# flip the first in-stock item to "was sold out"
for key, rec in s["items"].items():
    if rec["available"]:
        rec["available"] = False
        print("Faked sold-out for:", rec["name"])
        break
json.dump(s, open("state.json","w"), indent=2, ensure_ascii=False)
PY
python3 discord_bot.py
```

Next cycle, the bot sees that item flip from sold-out to in-stock and posts a
real restock alert. That confirms scraping, state, embeds, and channel
permissions all work together. Run `/check` to trigger it right away instead of
waiting.

---

## Step 9 — Keep it running

The bot has to stay running to catch anything, and a laptop that sleeps will
miss restocks. A cheap VPS or a Raspberry Pi is ideal.

**systemd** (Linux, survives reboots):

```ini
# /etc/systemd/system/matcha-bot.service
[Unit]
Description=Matcha restock Discord bot
After=network-online.target

[Service]
WorkingDirectory=/home/YOU/matcha-restock-bot
ExecStart=/usr/bin/python3 discord_bot.py
Restart=always
RestartSec=30
Environment=DISCORD_BOT_TOKEN=your_token_here
Environment=DISCORD_CHANNEL_ID=1122334455667788990

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now matcha-bot
journalctl -u matcha-bot -f      # watch the logs
```

**Quick and dirty** (any machine, survives you closing the terminal):

```bash
nohup python3 discord_bot.py > bot.log 2>&1 &
tail -f bot.log
```

Note the bot version needs a persistent process — unlike the webhook version,
you can't run it from cron.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Slash commands don't appear | Missing `applications.commands` scope. Re-run Step 5 with both scopes ticked, then restart the bot. |
| Commands still missing after that | You didn't set `discord_guild_id`, so they're global and take up to an hour. Set it and restart. |
| `LoginFailure: Improper token` | Token was copied wrong or has been reset. Get a fresh one from the Bot page. |
| Bot online but posts nothing | Wrong `discord_channel_id`, or it can't see that channel. Check channel permissions include Send Messages + Embed Links for the bot's role. |
| `Cannot access channel` in logs | Same as above — the ID is wrong or the bot isn't in that server. |
| `PrivilegedIntentsRequired` | You turned on an intent in Step 4. Turn all three off; this bot doesn't need any. |
| Alerts never fire | Nothing has actually restocked yet. Confirm the pipeline with the fake-restock test in Step 8. |
| Everything alerts at once | You deleted `state.json`, so it re-seeded. Harmless, happens once. |
| Bot drops offline during checks | Shouldn't happen — scraping runs in a worker thread. If it does, send me the log. |
| `HTTP 403` from either store | Being rate-limited or blocked. Raise `check_interval_seconds` and set a real contact address in `user_agent`. |
| Marukyu returns 0 products | Their page layout changed. `python3 bot.py --dump marukyu` and send me `dumps/marukyu_catalog.html`. |

---

## Marukyu detection: what `page-text` means

Marukyu sells most matcha in both 20g and 40g cans, and those sell out
independently. The bot tries three ways to read stock, best first:

1. `variations-json` — reads the exact per-size stock flags. Best.
2. `dom-variant` — reads per-size stock from the page markup. Also per-size.
3. `page-text` — only detects that *the product* is sold out, not which size.

Every alert footer and the `--test` output tells you which one fired. If you see
`page-text`, alerts still work, you just won't know which can returned. Marukyu
requires an account to shop, so the richer markup may only render when logged
in. If you want per-size accuracy, run `python3 bot.py --dump marukyu` and send
me `dumps/marukyu_product.html` — I can tighten the selectors to match.

---

## Cutting the check interval

The default is 300 seconds because a full Marukyu pass is ~50 requests. If you
narrow the watchlist, you can poll much faster without being rude:

```
/watch store:Marukyu Koyamaen term:Unkaku
/watch store:Marukyu Koyamaen term:Wako
```

With 2 teas instead of 50, drop `check_interval_seconds` to `90`. Watchlist
changes save to `config.json` and apply from the next cycle — no restart needed.

Worth knowing: Marukyu restocks reportedly sell out in as little as 30 minutes,
and you can't order at all without a registered account. Having an account
already set up and logged in matters more than shaving seconds off the poll.

---

## The webhook route (if you skip the bot)

1. Discord → right-click your channel → **Edit Channel** → **Integrations** →
   **Webhooks** → **New Webhook** → **Copy Webhook URL**.
2. Paste it into `discord_webhook_url` in `config.json`.
3. `python3 bot.py`

No application, no token, no invite, no admin rights. It can only post to that
one channel, which is all an alert needs. You lose the slash commands. It also
works from cron:

```
*/5 * * * * cd /path/to/matcha-restock-bot && /usr/bin/python3 bot.py --once >> bot.log 2>&1
```

Both versions share the same `state.json`, so don't run them at the same time
against the same file, or they'll consume each other's restock transitions.
