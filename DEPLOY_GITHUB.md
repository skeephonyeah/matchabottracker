# Deploying to GitHub — complete step by step

Everything from an empty GitHub account to a working restock watcher.
Follow the steps in order. Nothing here needs a server of your own.

**What you get:** Discord alerts when either shop restocks, naming the store,
the item, the size, and a direct link.

**What you don't get:** the `/status` and `/watch` slash commands. Those need a
process that stays connected to Discord, and a GitHub Actions job runs for a few
minutes and then the machine is destroyed. The code for that bot is in the repo
(`discord_bot.py`) and can be switched on later.

---

## Before you start

You need:

- A GitHub account — sign up free at github.com
- A Discord server where you can manage channels (you need the **Manage Webhooks**
  permission, which you have automatically on a server you created)
- The project files from this project folder

You do **not** need Python installed, a credit card, or a server.

---

## Step 1 — Create the Discord webhook

This is the address GitHub will post alerts to.

1. Open Discord (desktop or browser).
2. Decide which channel gets the alerts. Make one if you want — a dedicated
   `#matcha-restocks` channel is easier to mute or watch than a busy channel.
3. Hover the channel name in the left sidebar → click the **⚙️ gear** (Edit Channel).
4. In the left menu of that panel, click **Integrations**.
5. Click **Webhooks** → **New Webhook**.
6. Click the webhook that appears, then **Copy Webhook URL**.

Paste it somewhere temporary. It looks like:

```
https://discord.com/api/webhooks/1234567890/AbCdEf...
```

**Treat this like a password.** Anyone with it can post to your channel. It goes
into GitHub's encrypted secret storage in Step 4 — never into a file.

---

## Step 2 — Create the repository

1. Go to github.com and click the **+** in the top-right → **New repository**.
2. **Repository name:** `matcha-restock-bot`
3. **Visibility:** select **Public**.
4. Leave "Add a README file" **unticked** — the project already has one.
5. Click **Create repository**.

### Why public

GitHub Actions is free and unlimited on public repositories. Private
repositories get 2,000 minutes per month on the free plan, and this watcher uses
roughly 26,000 minutes a month checking every 5 minutes — about **$144/month** in
overage. On a private repo the free tier only stretches to about one check per
hour, which is useless for teas that sell out in 30 minutes.

Public is safe here:

- Your webhook goes in an encrypted **repository secret**, never in the code.
- GitHub masks secrets in workflow logs.
- The bot never prints the webhook anywhere.
- The workflow has a guard step that **fails the run** if it ever finds a real
  webhook URL committed in `config.json`.

The only thing a public repo reveals is which teas you're watching.

---

## Step 3 — Upload the files

Pick **3A** if you don't use the command line, **3B** if you do. Same result.

### 3A — Upload through the browser

On your new empty repository page, click **uploading an existing file**.

1. Open your project folder on your computer.
2. Select all the files and folders, and drag them into the browser window.
3. Wait for every file to finish uploading. You should see these at minimum:
   - `bot.py`, `state.py`, `notifier.py`
   - `requirements.txt`, `config.example.json`
   - the `stores` folder (with `common.py`, `marukyu.py`, `horii.py`, `__init__.py`)
   - the `.github` folder (with `workflows/restock.yml` and `workflows/keepalive.yml`)
4. In the "Commit changes" box type `Add matcha restock watcher`.
5. Click **Commit changes**.

**Important:** the `.github` folder starts with a dot, which some systems hide.
If it didn't upload, the workflow won't exist and nothing will ever run. Check
that you can see a `.github` folder in the file list on your repo page. If you
can't, click **Add file → Create new file**, type
`.github/workflows/restock.yml` as the filename (GitHub creates the folders as
you type the slashes), paste in the contents of that file, and commit.

### 3B — Upload with git

In a terminal, inside the project folder:

```bash
git init
git add .
git commit -m "Add matcha restock watcher"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/matcha-restock-bot.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your GitHub username. If it asks for a password,
use a personal access token, not your account password — GitHub will link you to
the page to create one.

### Either way, check for a leaked webhook before you continue

On the repository page, click into `config.example.json`. The line
`"discord_webhook_url"` must be empty (`""`). If your real webhook URL is in any
file, delete it, commit again, and go back to Step 1 to create a fresh webhook —
the exposed one should be considered compromised.

---

## Step 4 — Add your webhook as a secret

1. On your repository page click **Settings** (top row, far right).
2. In the left sidebar: **Secrets and variables** → **Actions**.
3. Click **New repository secret**.
4. **Name:** `DISCORD_WEBHOOK_URL` — exactly this, case-sensitive.
5. **Secret:** paste the webhook URL from Step 1.
6. Click **Add secret**.

The name must match exactly, because the workflow looks it up by that name. You
won't be able to read the value back afterwards — that's expected. If you lose
it, delete the secret and add it again.

---

## Step 5 — Turn Actions on

1. Click the **Actions** tab at the top of your repository.
2. If you see a green button saying **I understand my workflows, go ahead and
   enable them**, click it.
3. In the left sidebar you should now see **Matcha restock check** and
   **Keep schedule alive**.

If the left sidebar is empty, the `.github/workflows/` folder didn't upload
correctly — go back to Step 3.

---

## Step 6 — Do a dry run

This checks the scraping works without sending anything to Discord.

1. **Actions** tab → click **Matcha restock check** in the left sidebar.
2. On the right, click the **Run workflow** dropdown.
3. Tick **Print current stock without sending any Discord alerts**.
4. Click the green **Run workflow** button.
5. Wait ~10 seconds and refresh the page. A run appears with a yellow dot.
   Click it, then click the **check** job.
6. Expand the **Check for restocks** step.

You should see a stock listing for both shops:

```
=== Marukyu Koyamaen — 12/50 in stock ===
✅ IN STOCK  Unkaku — 20g can ¥3,700
             https://www.marukyu-koyamaen.co.jp/english/shop/products/1141020c1
❌ sold out  Kiwami Choan — 20g can ¥12,600
    (detection method: page-text)

=== Horii Shichimeien — 8/61 in stock ===
```

**Check three things:**

- Roughly 50 Marukyu products and 60 Horii products. Far fewer means the
  scraper needs adjusting.
- **No Discord message arrived.** Test mode sends nothing.
- The detection method. `shopify-json` for Horii is exact. For Marukyu,
  `variations-json` or `dom-variant` means per-size accuracy; `page-text` means
  stock is read per product rather than per size — you'll still be alerted, just
  without knowing which can came back.

If the run is red, open the failed step and read the error. Common causes are in
Troubleshooting below.

---

## Step 7 — Go live

1. **Actions** → **Matcha restock check** → **Run workflow** again.
2. This time leave the test box **unticked**.
3. Click **Run workflow**.

Within a minute or two you should get this in Discord:

> 🍵 Matcha restock watcher started. Recording current stock now — you'll get
> alerts from the next check onward.

**That message is your proof the whole chain works** — secret, webhook, channel.
If it doesn't arrive, the problem is the secret or the webhook, not the scraper.

This run records what's currently in stock and deliberately alerts on nothing
else. Otherwise you'd get pinged for every item that already happens to be in
stock, which isn't a restock. Real alerts begin from the next check.

---

## Step 8 — Confirm the schedule took over

The workflow now runs itself every 5 minutes. Scheduled runs usually start
firing within 10–15 minutes of the first push.

Go to the **Actions** tab in an hour. You should see a list of runs whose
**Event** column reads `schedule` rather than `workflow_dispatch`. That's it —
it's live, and you can close the tab.

### What to expect from the timing

GitHub's scheduler is best-effort. Five minutes is the platform minimum, and
runs are commonly delayed 5–30 minutes at busy times and are occasionally
skipped entirely with no notification. The workflow already fires at minutes
4, 9, 14… instead of on the hour, because the top of the hour is the most
congested slot and odd minutes get serviced faster.

Be realistic: this catches many restocks and will miss some.

---

## Step 9 — Narrow the watchlist (recommended)

By default the watcher checks every Marukyu product, which means ~50 page
fetches per run. Narrowing it makes runs much faster and is gentler on the shop.

1. On your repository page, click **Add file** → **Create new file**.
2. Name it `config.json`.
3. Paste in the contents of `config.example.json`, then edit the two
   `watchlist` lines:

```json
"stores": {
  "marukyu": {
    "enabled": true,
    "request_delay_seconds": 1.5,
    "catalog_urls": [
      "https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha?viewall=1"
    ],
    "watchlist": ["Unkaku", "Wako", "Kinrin", "Aoarashi", "Isuzu"]
  },
  "horii": {
    "enabled": true,
    "request_delay_seconds": 1.0,
    "watchlist": ["Matcha"]
  }
}
```

4. Leave `"discord_webhook_url": ""` empty. It comes from the secret.
5. Commit the file.

Terms match anywhere in the product name, case-insensitively. An **empty**
watchlist means "track everything" — so adding your first term narrows what's
tracked rather than adding to it.

Horii costs one request no matter what, because it reads the whole shop feed at
once. Only Marukyu benefits from narrowing.

### Optional: get a phone notification

In `config.json`, set `mention` to your Discord user ID:

```json
"mention": "<@123456789012345678>"
```

To find your ID: Discord **Settings** → **Advanced** → turn on **Developer
Mode**, then right-click your own name → **Copy User ID**. Make sure
notifications are enabled for that channel on your phone.

---

## Step 10 — Change how often it checks (optional)

Edit `.github/workflows/restock.yml` on GitHub (click the file, then the ✏️
pencil icon) and change the `cron:` line:

```yaml
- cron: "4,14,24,34,44,54 * * * *"     # every 10 minutes
- cron: "4,19,34,49 * * * *"           # every 15 minutes
```

Keep the odd-minute offsets rather than using `*/10`, for the congestion reason
above. Five minutes is the fastest GitHub allows.

---

## Why there are two workflows

`restock.yml` does the checking. `keepalive.yml` makes one tiny commit every
Monday, because **GitHub disables scheduled workflows after 60 days of
repository inactivity** and scheduled runs don't count as activity. Without it
your watcher would quietly stop after two months and you'd only notice by the
silence. Leave it alone.

---

## How it remembers between runs

Each run gets a fresh machine, so `state.json` — the record of what was in stock
last time — is stored in the GitHub Actions cache and restored at the start of
the next run. That comparison is what makes a restock detectable.

If the cache is ever lost, the bot finds no previous state, treats that run as a
first run, records everything silently, and resumes alerting next cycle. You
lose one cycle of alerts; you never get a flood of false ones.

---

## Troubleshooting

**No Discord message after Step 7.**
Check the secret is named exactly `DISCORD_WEBHOOK_URL`. Open the run log — if
it says "No Discord webhook configured", the secret isn't being found. Recreate
it in Step 4.

**The run fails on "Refuse to run if a webhook URL was committed".**
You committed a real webhook URL in `config.json`. Delete the webhook in Discord
immediately (Edit Channel → Integrations → Webhooks → delete), create a new one,
remove the URL from the file, and update the secret with the new URL.

**Runs show 0 products, or the run is red on "Check for restocks".**
The shop changed its page layout. Run the workflow with test mode on and read
the log to see which shop failed.

**No scheduled runs appear, only ones you started manually.**
Scheduled workflows only run from the default branch — make sure the files are
on `main`. Also check **Settings → Actions → General** that Actions aren't
disabled for the repository.

**Runs are consistently late.**
That's the platform, not your setup. There's no way to make GitHub's scheduler
exact.

**Alerts stopped after about two months.**
The keepalive workflow was deleted or is failing. Re-enable the schedule from
the Actions tab.

**I'm getting alerts for teas I don't care about.**
Do Step 9.

**I want to start over.**
Delete the repository (Settings → scroll to the bottom → Delete this repository)
and begin again from Step 2. Nothing is stored outside it.
