"""Exercise the slash commands without ever connecting to Discord.

Every command callback is a plain coroutine, so we can call it directly with a
stand-in Interaction and assert on what it would have replied.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


CONFIG = {
    "discord_bot_token": "fake-token-for-testing",
    "discord_channel_id": 123456789,
    "check_interval_seconds": 300,
    "state_file": "state.json",
    "stores": {
        "marukyu": {"enabled": True, "watchlist": []},
        "horii": {"enabled": True, "watchlist": []},
    },
}


class FakeResponse:
    def __init__(self):
        self.messages = []
        self.deferred = False

    async def send_message(self, content=None, embed=None, ephemeral=False, **kwargs):
        self.messages.append({"content": content, "embed": embed, "ephemeral": ephemeral})

    async def defer(self, **kwargs):
        self.deferred = True


class FakeInteraction:
    def __init__(self):
        self.response = FakeResponse()

    @property
    def last(self):
        return self.response.messages[-1]


def choice(value, name):
    return app_commands.Choice(name=name, value=value)


async def main() -> None:
    workdir = tempfile.mkdtemp()
    original = os.getcwd()
    os.chdir(workdir)

    with open("config.json", "w") as handle:
        json.dump(CONFIG, handle)

    os.environ["MATCHA_CONFIG"] = os.path.join(workdir, "config.json")
    import discord_bot  # imported after cwd/env are set

    discord_bot.CONFIG_PATH = os.path.join(workdir, "config.json")

    config = discord_bot.load_config()
    bot = discord_bot.MatchaBot(config)
    discord_bot.register_commands(bot)

    # ---------------------------------------------------------------- tree
    print("\nCommand registration")
    names = {cmd.name for cmd in bot.tree.get_commands()}
    for expected in ("status", "stock", "check", "watch", "unwatch", "watchlist"):
        check(f"/{expected} is registered", expected in names, sorted(names))

    commands = {cmd.name: cmd for cmd in bot.tree.get_commands()}
    watch_params = {p.name for p in commands["watch"].parameters}
    check("/watch takes store and term", watch_params == {"store", "term"}, watch_params)

    store_param = next(p for p in commands["watch"].parameters if p.name == "store")
    check(
        "/watch offers both shops as choices",
        {c.value for c in store_param.choices} == {"marukyu", "horii"},
    )
    check("no privileged intents needed", not bot.intents.message_content)

    # -------------------------------------------------------------- status
    print("\n/status before any check has run")
    interaction = FakeInteraction()
    await commands["status"].callback(interaction)
    embed = interaction.last["embed"]
    fields = {f.name: f.value for f in embed.fields}
    check("replies with an embed", embed is not None)
    check("reports no check yet", fields.get("Last check") == "not yet", fields)
    check("reports zero items tracked", fields.get("Items tracked") == "0", fields)
    check("says seeding is pending", "no" in fields.get("Seeded", ""), fields)
    check("reply is ephemeral (only you see it)", interaction.last["ephemeral"] is True)

    # --------------------------------------------------------------- stock
    print("\n/stock with an empty state")
    interaction = FakeInteraction()
    await commands["stock"].callback(interaction, None)
    check("says nothing is in stock", "Nothing in stock" in interaction.last["content"])

    # Seed some state and try again.
    bot.state.data = {
        "marukyu:1141020C1": {
            "available": True, "name": "Unkaku", "variant": "20g can",
            "store": "Marukyu Koyamaen", "price": "¥3,700",
            "url": "https://www.marukyu-koyamaen.co.jp/english/shop/products/1141020c1",
        },
        "horii:1:2": {
            "available": False, "name": "Matcha Okunoyama", "variant": None,
            "store": "Horii Shichimeien", "price": "$31.00",
            "url": "https://horiishichimeien.com/en-sb/products/matcha-okunoyama",
        },
    }
    interaction = FakeInteraction()
    await commands["stock"].callback(interaction, None)
    body = interaction.last["content"]
    check("lists the in-stock item", "Unkaku" in body)
    check("includes its direct link", "1141020c1" in body)
    check("omits the sold-out item", "Okunoyama" not in body)

    interaction = FakeInteraction()
    await commands["stock"].callback(interaction, choice("horii", "Horii Shichimeien"))
    check("store filter excludes the other shop", "Unkaku" not in interaction.last["content"])

    # --------------------------------------------------------------- watch
    print("\n/watch narrowing behaviour")
    interaction = FakeInteraction()
    await commands["watch"].callback(
        interaction, choice("marukyu", "Marukyu Koyamaen"), "Unkaku"
    )
    reply = interaction.last["content"]
    check("first term warns that tracking narrows", "Heads up" in reply, reply[:120])
    check("explains only matching items are tracked", "only" in reply.lower())
    check("watchlist updated in memory", config["stores"]["marukyu"]["watchlist"] == ["Unkaku"])

    with open(discord_bot.CONFIG_PATH) as handle:
        on_disk = json.load(handle)
    check("watchlist persisted to config.json", on_disk["stores"]["marukyu"]["watchlist"] == ["Unkaku"])
    check("token not rewritten into config", on_disk.get("discord_bot_token") == "fake-token-for-testing")

    interaction = FakeInteraction()
    await commands["watch"].callback(interaction, choice("marukyu", "Marukyu Koyamaen"), "Wako")
    reply = interaction.last["content"]
    check("second term does NOT warn", "Heads up" not in reply)
    check("second term lists both terms", "Unkaku" in reply and "Wako" in reply)

    interaction = FakeInteraction()
    await commands["watch"].callback(interaction, choice("marukyu", "Marukyu Koyamaen"), "unkaku")
    check("duplicate term rejected case-insensitively", "already" in interaction.last["content"])
    check("watchlist unchanged after duplicate", len(config["stores"]["marukyu"]["watchlist"]) == 2)

    # ------------------------------------------------------------- unwatch
    print("\n/unwatch")
    interaction = FakeInteraction()
    await commands["unwatch"].callback(interaction, choice("marukyu", "Marukyu Koyamaen"), "nope")
    check("removing a missing term is handled", "isn't on" in interaction.last["content"])

    interaction = FakeInteraction()
    await commands["unwatch"].callback(interaction, choice("marukyu", "Marukyu Koyamaen"), "WAKO")
    check("removal is case-insensitive", "Removed" in interaction.last["content"])

    interaction = FakeInteraction()
    await commands["unwatch"].callback(interaction, choice("marukyu", "Marukyu Koyamaen"), "Unkaku")
    check(
        "removing the last term restores tracking everything",
        "everything" in interaction.last["content"],
        interaction.last["content"],
    )

    # ----------------------------------------------------------- watchlist
    print("\n/watchlist")
    interaction = FakeInteraction()
    await commands["watchlist"].callback(interaction)
    fields = {f.name: f.value for f in interaction.last["embed"].fields}
    check("shows both shops", len(fields) == 2, list(fields))
    check("empty watchlist reads as everything", "everything" in fields["Marukyu Koyamaen"])

    # --------------------------------------------------------------- check
    print("\n/check while a scrape is already running")
    await bot.checking.acquire()
    interaction = FakeInteraction()
    await commands["check"].callback(interaction)
    check("refuses to start a second scrape", "already running" in interaction.last["content"])
    check("did not defer", interaction.response.deferred is False)
    bot.checking.release()

    os.chdir(original)
    shutil.rmtree(workdir, ignore_errors=True)


asyncio.run(main())
print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)
