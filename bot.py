#!/usr/bin/env python3
"""Matcha restock watcher for Marukyu Koyamaen and Horii Shichimeien.

Usage:
    python bot.py                 # run forever, alerting on restocks
    python bot.py --once          # single pass (good for cron / testing)
    python bot.py --test          # show current stock, send nothing
    python bot.py --dump marukyu  # save raw HTML/JSON for selector debugging

Configuration lives in config.json (see config.example.json). The Discord
webhook may also come from the DISCORD_WEBHOOK_URL environment variable.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from typing import Callable

import notifier
from state import StockState
from stores import horii, marukyu
from stores.common import Item, StoreError, make_session

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DEFAULT_CONFIG_PATH = "config.json"

STORES: dict[str, Callable] = {
    "marukyu": marukyu.fetch,
    "horii": horii.fetch,
}

log = logging.getLogger("bot")


# ----------------------------------------------------------------------
DEFAULT_CONFIG = {
    "state_file": "state.json",
    "check_interval_seconds": 300,
    "jitter_seconds": 30,
    "announce_startup": True,
    "report_errors_to_discord": True,
    "stores": {
        "marukyu": {"enabled": True, "request_delay_seconds": 1.5, "watchlist": []},
        "horii": {"enabled": True, "request_delay_seconds": 1.0, "watchlist": []},
    },
}


def load_config(path: str, require_webhook: bool = True) -> dict:
    """Load config.json if it exists, otherwise fall back to defaults.

    The file is optional on purpose: a GitHub Actions setup only needs the
    DISCORD_WEBHOOK_URL secret, with no config file committed to the repo.
    """
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    else:
        log.info("No %s found -- using built-in defaults", path)
        config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy

    env_hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if env_hook:
        config["discord_webhook_url"] = env_hook

    # --test and --dump never send anything, so they don't need a webhook.
    # That matters if you only intend to run the slash-command bot.
    if require_webhook and not config.get("discord_webhook_url"):
        sys.exit(
            "No Discord webhook configured. Set 'discord_webhook_url' in "
            "config.json or the DISCORD_WEBHOOK_URL environment variable.\n"
            "(If you're using discord_bot.py instead, you can still run "
            "'python bot.py --test' to check scraping.)"
        )
    return config


def build_session(config: dict):
    ua = config.get(
        "user_agent",
        "MatchaRestockWatcher/1.0 (personal restock notifier; contact: you@example.com)",
    )
    return make_session(ua)


# ----------------------------------------------------------------------
def poll_store(name: str, session, config: dict) -> list[Item]:
    store_config = config.get("stores", {}).get(name, {})
    if not store_config.get("enabled", True):
        return []
    return STORES[name](session, store_config)


def run_cycle(config: dict, state: StockState, session, dry_run: bool = False) -> list[Item]:
    all_items: list[Item] = []
    failures: list[str] = []

    for name in STORES:
        try:
            items = poll_store(name, session, config)
            all_items.extend(items)
        except StoreError as exc:
            log.error("%s: %s", name, exc)
            failures.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.exception("%s: unexpected error", name)
            failures.append(f"{name}: {exc}")

    if failures and config.get("report_errors_to_discord", True) and not dry_run:
        notifier.notify_text(
            config["discord_webhook_url"],
            "⚠️ Restock watcher had trouble this cycle:\n"
            + "\n".join(f"• {f}" for f in failures),
        )

    if dry_run:
        return all_items

    restocked = state.diff(all_items)

    if restocked:
        log.info("RESTOCK: %d item(s) came back in stock", len(restocked))
        for item in restocked:
            log.info("  -> [%s] %s | %s", item.store, item.label, item.url)
        notifier.notify_restocks(
            config["discord_webhook_url"],
            restocked,
            mention=config.get("mention", ""),
        )
    else:
        log.info("No restocks this cycle (%d items checked)", len(all_items))

    return restocked


# ----------------------------------------------------------------------
def print_stock_table(items: list[Item]) -> None:
    if not items:
        print("No items found.")
        return

    by_store: dict[str, list[Item]] = {}
    for item in items:
        by_store.setdefault(item.store, []).append(item)

    for store, store_items in by_store.items():
        in_stock = [i for i in store_items if i.available]
        print(f"\n=== {store} — {len(in_stock)}/{len(store_items)} in stock ===")
        for item in sorted(store_items, key=lambda i: (not i.available, i.name)):
            mark = "✅ IN STOCK " if item.available else "❌ sold out "
            price = f" {item.price}" if item.price else ""
            print(f"{mark} {item.label}{price}")
            if item.available:
                print(f"             {item.url}")
        methods = {i.detection for i in store_items}
        print(f"    (detection method: {', '.join(sorted(methods))})")


def dump_raw(store: str, config: dict) -> None:
    """Save raw responses so selectors can be checked against real markup."""
    session = build_session(config)
    os.makedirs("dumps", exist_ok=True)

    if store == "marukyu":
        url = marukyu.DEFAULT_CATALOGS[0]
        resp = session.get(url, timeout=30)
        with open("dumps/marukyu_catalog.html", "w", encoding="utf-8") as handle:
            handle.write(resp.text)
        products = marukyu.discover_products(session, marukyu.DEFAULT_CATALOGS, 1.5)
        if products:
            product_url, _ = products[0]
            resp = session.get(product_url, timeout=30)
            with open("dumps/marukyu_product.html", "w", encoding="utf-8") as handle:
                handle.write(resp.text)
            print(f"Wrote dumps/marukyu_catalog.html and dumps/marukyu_product.html ({product_url})")
    elif store == "horii":
        resp = session.get(f"{horii.PRODUCTS_JSON}?limit=250&page=1", timeout=30)
        with open("dumps/horii_products.json", "w", encoding="utf-8") as handle:
            handle.write(resp.text)
        print("Wrote dumps/horii_products.json")
    else:
        sys.exit(f"Unknown store '{store}'. Use 'marukyu' or 'horii'.")


# ----------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Matcha restock watcher")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--test", action="store_true", help="print current stock, send nothing")
    parser.add_argument("--dump", metavar="STORE", help="save raw response for debugging")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=LOG_FORMAT,
        datefmt="%H:%M:%S",
    )

    if args.dump:
        config = load_config(args.config) if os.path.exists(args.config) else {}
        dump_raw(args.dump, config)
        return

    config = load_config(args.config, require_webhook=not args.test)
    session = build_session(config)

    if args.test:
        items = run_cycle(config, StockState(config.get("state_file", "state.json")),
                          session, dry_run=True)
        print_stock_table(items)
        return

    state = StockState(config.get("state_file", "state.json"))

    if config.get("announce_startup", True) and not state.seeded:
        notifier.notify_text(
            config["discord_webhook_url"],
            "🍵 Matcha restock watcher started. Recording current stock now — "
            "you'll get alerts from the next check onward.",
        )

    interval = int(config.get("check_interval_seconds", 300))
    jitter = int(config.get("jitter_seconds", 30))

    if args.once:
        run_cycle(config, state, session)
        return

    log.info("Watching every ~%ds (± %ds). Ctrl-C to stop.", interval, jitter)
    consecutive_failures = 0

    while True:
        started = time.time()
        try:
            run_cycle(config, state, session)
            consecutive_failures = 0
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            return
        except Exception:  # noqa: BLE001
            consecutive_failures += 1
            log.exception("Cycle failed (%d in a row)", consecutive_failures)

        # Back off if something is persistently broken.
        wait = interval + random.randint(-jitter, jitter)
        if consecutive_failures:
            wait = min(3600, wait * (2**consecutive_failures))

        elapsed = time.time() - started
        sleep_for = max(30, wait - elapsed)
        log.debug("Sleeping %.0fs", sleep_for)
        try:
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            return


if __name__ == "__main__":
    main()
