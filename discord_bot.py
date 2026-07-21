#!/usr/bin/env python3
"""Matcha restock watcher as a real Discord bot (slash commands + alerts).

This is the bot-token version. It does everything the webhook version does,
plus slash commands you can run in Discord:

    /status      is the watcher alive, when is the next check
    /stock       what is in stock right now
    /check       force a check immediately
    /watch       add a tea to the watchlist
    /unwatch     remove one
    /watchlist   show the current watchlist

Scraping is blocking and can take ~90 seconds, so every scrape runs in a
worker thread via asyncio.to_thread(). If it ran on the event loop directly
it would stall Discord's heartbeat and the bot would drop offline mid-check.

Run with:  python discord_bot.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks

import notifier
from state import StockState
from stores import horii, marukyu
from stores.common import Item, StoreError, make_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("discord_bot")

CONFIG_PATH = os.environ.get("MATCHA_CONFIG", "config.json")
STORES = {"marukyu": marukyu.fetch, "horii": horii.fetch}


# ----------------------------------------------------------------------
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"Config file '{CONFIG_PATH}' not found. Copy config.example.json first.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    # Environment variables win, so you can keep secrets out of the file.
    if os.environ.get("DISCORD_BOT_TOKEN"):
        config["discord_bot_token"] = os.environ["DISCORD_BOT_TOKEN"]
    if os.environ.get("DISCORD_CHANNEL_ID"):
        config["discord_channel_id"] = int(os.environ["DISCORD_CHANNEL_ID"])

    if not config.get("discord_bot_token"):
        sys.exit(
            "No bot token. Set 'discord_bot_token' in config.json or the "
            "DISCORD_BOT_TOKEN environment variable."
        )
    if not config.get("discord_channel_id"):
        sys.exit(
            "No channel. Set 'discord_channel_id' in config.json or the "
            "DISCORD_CHANNEL_ID environment variable.\n"
            "In Discord: enable Developer Mode, then right-click the channel -> Copy Channel ID."
        )
    return config


def save_config(config: dict) -> None:
    """Persist watchlist edits made through slash commands."""
    saveable = {k: v for k, v in config.items() if k not in ("discord_bot_token",)}
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        on_disk = json.load(handle)
    on_disk.update(saveable)
    # Never write the token back if it came from the environment.
    if os.environ.get("DISCORD_BOT_TOKEN"):
        on_disk.pop("discord_bot_token", None)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(on_disk, handle, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------
class MatchaBot(discord.Client):
    def __init__(self, config: dict):
        # Slash commands need no privileged intents at all.
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.config = config
        self.state = StockState(config.get("state_file", "state.json"))
        self.session = make_session(
            config.get("user_agent", "MatchaRestockWatcher/1.0 (personal restock notifier)")
        )
        self.last_check: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_items: list[Item] = []
        self.checking = asyncio.Lock()

    async def setup_hook(self) -> None:
        guild_id = self.config.get("discord_guild_id")
        if guild_id:
            # Guild-scoped commands appear instantly; global ones take ~1 hour.
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands synced to guild %s", guild_id)
        else:
            await self.tree.sync()
            log.info("Slash commands synced globally (may take up to an hour to appear)")

        # A full Marukyu pass takes ~90s when tracking every product. Polling
        # faster than that just queues cycles behind the lock and hammers the
        # shop for no extra speed, so enforce a floor.
        interval = max(120, int(self.config.get("check_interval_seconds", 300)))
        if interval != self.config.get("check_interval_seconds"):
            log.info("Poll interval clamped to %ds", interval)
        self.poll_loop.change_interval(seconds=interval)
        self.poll_loop.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="matcha stock")
        )

    # ------------------------------------------------------------------
    def _scrape_all(self) -> tuple[list[Item], list[str]]:
        """Blocking. Always called through asyncio.to_thread()."""
        items: list[Item] = []
        errors: list[str] = []
        for name, fetch in STORES.items():
            store_config = self.config.get("stores", {}).get(name, {})
            if not store_config.get("enabled", True):
                continue
            try:
                items.extend(fetch(self.session, store_config))
            except StoreError as exc:
                errors.append(f"{name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                log.exception("%s failed", name)
                errors.append(f"{name}: {exc}")
        return items, errors

    async def run_check(self, announce: bool = True) -> tuple[list[Item], list[Item], list[str]]:
        """Scrape, diff against saved state, and post any restocks."""
        async with self.checking:
            items, errors = await asyncio.to_thread(self._scrape_all)
            self.last_check = time.time()
            self.last_error = "; ".join(errors) if errors else None
            self.last_items = items

            restocked = self.state.diff(items) if items else []

            if restocked and announce:
                await self.post_restocks(restocked)
            if errors and self.config.get("report_errors_to_discord", True):
                await self.post_text("⚠️ Trouble this cycle:\n" + "\n".join(f"• {e}" for e in errors))

            return items, restocked, errors

    # ------------------------------------------------------------------
    async def get_channel_safe(self) -> Optional[discord.abc.Messageable]:
        channel_id = int(self.config["discord_channel_id"])
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden) as exc:
                log.error("Cannot access channel %s: %s", channel_id, exc)
                return None
        return channel

    async def post_restocks(self, restocked: list[Item]) -> None:
        channel = await self.get_channel_safe()
        if channel is None:
            return

        mention = self.config.get("mention", "")
        restocked.sort(key=lambda i: (i.store, i.name, i.variant or ""))

        for start in range(0, len(restocked), 10):  # Discord caps embeds at 10
            chunk = restocked[start : start + 10]
            stores = sorted({i.store for i in chunk})
            content = f"**Restock alert — {', '.join(stores)}** ({len(chunk)} item(s))"
            if mention:
                content = f"{mention} {content}"

            embeds = [discord.Embed.from_dict(notifier.build_embed(i)) for i in chunk]
            await channel.send(
                content=content,
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions.all()
                if mention
                else discord.AllowedMentions.none(),
            )
            await asyncio.sleep(1)

    async def post_text(self, message: str) -> None:
        channel = await self.get_channel_safe()
        if channel:
            await channel.send(
                message[:1900], allowed_mentions=discord.AllowedMentions.none()
            )

    # ------------------------------------------------------------------
    @tasks.loop(seconds=300)
    async def poll_loop(self) -> None:
        try:
            _, restocked, _ = await self.run_check()
            log.info("Cycle done — %d restock(s)", len(restocked))
        except Exception:  # noqa: BLE001 - never let the loop die
            log.exception("Poll cycle blew up")

    @poll_loop.before_loop
    async def before_poll(self) -> None:
        await self.wait_until_ready()
        if not self.state.seeded and self.config.get("announce_startup", True):
            await self.post_text(
                "🍵 Matcha restock watcher online. Recording current stock now — "
                "alerts start from the next check."
            )


# ----------------------------------------------------------------------
def register_commands(bot: MatchaBot) -> None:
    tree = bot.tree

    @tree.command(name="status", description="Is the watcher running, and when is the next check?")
    async def status(interaction: discord.Interaction) -> None:
        interval = int(bot.config.get("check_interval_seconds", 300))
        if bot.last_check:
            ago = int(time.time() - bot.last_check)
            last = f"<t:{int(bot.last_check)}:R> ({ago}s ago)"
            nxt = f"<t:{int(bot.last_check + interval)}:R>"
        else:
            last = "not yet"
            nxt = "shortly"

        tracked = len(bot.state.data)
        in_stock = len(bot.state.in_stock_summary())

        embed = discord.Embed(title="🍵 Restock watcher status", color=0x4CAF50)
        embed.add_field(name="Last check", value=last, inline=True)
        embed.add_field(name="Next check", value=nxt, inline=True)
        embed.add_field(name="Interval", value=f"{interval}s", inline=True)
        embed.add_field(name="Items tracked", value=str(tracked), inline=True)
        embed.add_field(name="Currently in stock", value=str(in_stock), inline=True)
        embed.add_field(
            name="Seeded",
            value="yes" if bot.state.seeded else "no (first cycle pending)",
            inline=True,
        )
        if bot.last_error:
            embed.add_field(name="⚠️ Last error", value=bot.last_error[:1000], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="stock", description="Show what is in stock right now")
    @app_commands.describe(store="Limit to one store")
    @app_commands.choices(
        store=[
            app_commands.Choice(name="Both stores", value="both"),
            app_commands.Choice(name="Marukyu Koyamaen", value="marukyu"),
            app_commands.Choice(name="Horii Shichimeien", value="horii"),
        ]
    )
    async def stock(
        interaction: discord.Interaction,
        store: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        choice = store.value if store else "both"
        records = bot.state.in_stock_summary()

        if choice != "both":
            wanted = "Marukyu" if choice == "marukyu" else "Horii"
            records = [r for r in records if wanted in (r.get("store") or "")]

        if not records:
            await interaction.response.send_message(
                "Nothing in stock right now. 😔", ephemeral=True
            )
            return

        lines = []
        for record in sorted(records, key=lambda r: (r.get("store", ""), r.get("name", ""))):
            label = record.get("name", "?")
            if record.get("variant"):
                label += f" — {record['variant']}"
            price = f" ({record['price']})" if record.get("price") else ""
            lines.append(f"• **{label}**{price}\n  {record.get('url', '')}")

        text = f"**In stock now ({len(records)}):**\n" + "\n".join(lines)
        # Discord hard-caps messages at 2000 characters.
        if len(text) > 1950:
            text = text[:1900].rsplit("\n", 1)[0] + f"\n…and more. Total: {len(records)}."
        await interaction.response.send_message(text, ephemeral=True)

    @tree.command(name="check", description="Force a stock check right now")
    async def check(interaction: discord.Interaction) -> None:
        if bot.checking.locked():
            await interaction.response.send_message(
                "A check is already running — hold on.", ephemeral=True
            )
            return

        # Scraping takes well over Discord's 3-second reply window.
        await interaction.response.defer(thinking=True, ephemeral=True)
        started = time.time()
        items, restocked, errors = await bot.run_check()
        took = time.time() - started

        summary = (
            f"Checked **{len(items)}** items in {took:.0f}s.\n"
            f"Restocks found: **{len(restocked)}**"
        )
        if errors:
            summary += "\n⚠️ " + "; ".join(errors)[:500]
        await interaction.followup.send(summary, ephemeral=True)

    # -- watchlist management ------------------------------------------
    def watchlist_for(store_key: str) -> list[str]:
        return bot.config.setdefault("stores", {}).setdefault(store_key, {}).setdefault(
            "watchlist", []
        )

    store_choices = [
        app_commands.Choice(name="Marukyu Koyamaen", value="marukyu"),
        app_commands.Choice(name="Horii Shichimeien", value="horii"),
    ]

    @tree.command(name="watch", description="Only alert on teas matching this name")
    @app_commands.describe(store="Which store", term="Part of the tea's name, e.g. Unkaku")
    @app_commands.choices(store=store_choices)
    async def watch(
        interaction: discord.Interaction,
        store: app_commands.Choice[str],
        term: str,
    ) -> None:
        watchlist = watchlist_for(store.value)
        if term.lower() in [w.lower() for w in watchlist]:
            await interaction.response.send_message(
                f"`{term}` is already on the {store.name} watchlist.", ephemeral=True
            )
            return
        # An empty watchlist means "track everything", so the FIRST term added
        # narrows tracking rather than widening it. That surprises people, so
        # say it out loud rather than silently dropping ~50 products.
        was_tracking_everything = not watchlist
        watchlist.append(term)
        save_config(bot.config)

        if was_tracking_everything:
            message = (
                f"Added `{term}` to the **{store.name}** watchlist.\n\n"
                f"⚠️ **Heads up:** that store was tracking *everything*. It will now "
                f"track **only** products whose name contains `{term}` — every other "
                f"tea there stops being watched. Add more terms to widen it, or "
                f"`/unwatch {term}` to go back to tracking everything."
            )
        else:
            terms = ", ".join(f"`{w}`" for w in watchlist)
            message = (
                f"Added `{term}` to the **{store.name}** watchlist "
                f"({len(watchlist)} terms): {terms}"
            )

        await interaction.response.send_message(
            message + "\n\nApplies from the next check — run `/check` to apply it now.",
            ephemeral=True,
        )

    @tree.command(name="unwatch", description="Stop filtering on a term")
    @app_commands.describe(store="Which store", term="The term to remove")
    @app_commands.choices(store=store_choices)
    async def unwatch(
        interaction: discord.Interaction,
        store: app_commands.Choice[str],
        term: str,
    ) -> None:
        watchlist = watchlist_for(store.value)
        match = next((w for w in watchlist if w.lower() == term.lower()), None)
        if not match:
            await interaction.response.send_message(
                f"`{term}` isn't on the {store.name} watchlist.", ephemeral=True
            )
            return
        watchlist.remove(match)
        save_config(bot.config)
        note = " — now tracking everything at that store." if not watchlist else ""
        await interaction.response.send_message(
            f"Removed `{match}` from the **{store.name}** watchlist.{note}", ephemeral=True
        )

    @tree.command(name="watchlist", description="Show the current watchlists")
    async def watchlist_cmd(interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="🍵 Watchlists", color=0x4CAF50)
        for key, label in (("marukyu", "Marukyu Koyamaen"), ("horii", "Horii Shichimeien")):
            terms = watchlist_for(key)
            embed.add_field(
                name=label,
                value="\n".join(f"• {t}" for t in terms) if terms else "*everything*",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ----------------------------------------------------------------------
def main() -> None:
    config = load_config()
    bot = MatchaBot(config)
    register_commands(bot)
    bot.run(config["discord_bot_token"], log_handler=None)


if __name__ == "__main__":
    main()
