import json
import logging
import os
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

from analyzer import FlipDeal
from item_catalog import ItemCatalog
from key_pool import KeyPool, SAFE_DEFAULT_MAX_RPM, SAFE_MAX_RPM, SAFE_MIN_RPM
from torn_api import TornApiClient
from watcher import FlipWatcher

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("TornFlipper")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
PRESETS_PATH = BASE_DIR / "presets.json"
DEFAULT_RPM = int(os.getenv("DEFAULT_KEY_MAX_RPM", str(SAFE_DEFAULT_MAX_RPM)))
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL_SECONDS", "3.0"))
DEFAULT_MIN_PROFIT_PCT = float(os.getenv("DEFAULT_MIN_PROFIT_PCT", "3.0"))


def load_presets(presets_path: Path = PRESETS_PATH) -> dict[str, list[dict]]:
    if not presets_path.exists():
        return {}
    return json.loads(presets_path.read_text(encoding="utf-8"))


class TornFlipperBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.key_pool = KeyPool(storage_path=DATA_DIR / "keys.json")
        self.torn_api = TornApiClient()
        self.catalog = ItemCatalog(
            torn_api=self.torn_api,
            key_pool=self.key_pool,
            storage_path=DATA_DIR / "catalog.json",
        )
        self.watcher = FlipWatcher(
            key_pool=self.key_pool,
            catalog=self.catalog,
            torn_api=self.torn_api,
            storage_path=DATA_DIR / "flip_state.json",
            scan_interval=SCAN_INTERVAL,
            on_deal=self.handle_deal,
            on_notice=self.handle_notice,
        )

    async def setup_hook(self) -> None:
        synced = await self.tree.sync()
        logger.info("Synced %d global command(s): %s", len(synced), [c.name for c in synced])
        if self.key_pool.active_count() > 0 and self.catalog.is_stale():
            await self.catalog.refresh()
        if self.watcher.watching and self.watcher.alert_channel_id:
            self.watcher.start(self.watcher.alert_channel_id)

    async def close(self) -> None:
        self.watcher.stop()
        await self.torn_api.close()
        await super().close()

    async def handle_deal(self, deal: FlipDeal) -> None:
        channel_id = self.watcher.alert_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return

        pings = " ".join(f"<@{uid}>" for uid in sorted(self.watcher.subscribers))
        color = discord.Color.orange() if deal.is_high_mug_risk else discord.Color.green()

        bazaar_sign = "+" if deal.bazaar_profit_per_unit >= 0 else "-"
        imarket_sign = "+" if deal.imarket_profit_per_unit >= 0 else "-"

        embed = discord.Embed(
            title=f"💰 Flip Deal: {deal.item_name} (x{deal.amount:,})",
            url=deal.market_url,
            color=color,
        )
        embed.add_field(
            name="Listed Price",
            value=f"**${deal.listed_price:,}** (-{deal.discount_pct:.1f}%)",
            inline=True,
        )
        embed.add_field(
            name="Torn Market Value",
            value=f"${deal.market_value:,}",
            inline=True,
        )
        embed.add_field(
            name="Total Cost",
            value=f"${deal.total_cost:,}",
            inline=True,
        )
        embed.add_field(
            name="Bazaar Flip (0% fee)",
            value=(
                f"**{bazaar_sign}${abs(deal.bazaar_profit_per_unit):,}/ea**\n"
                f"({bazaar_sign}${abs(deal.bazaar_total_profit):,} total)"
            ),
            inline=True,
        )
        embed.add_field(
            name="Item Market Flip (5% fee)",
            value=(
                f"{imarket_sign}${abs(deal.imarket_profit_per_unit):,}/ea\n"
                f"({imarket_sign}${abs(deal.imarket_total_profit):,} total)"
            ),
            inline=True,
        )
        if deal.is_high_mug_risk and deal.mug_risk_reason:
            embed.add_field(name="⚠️ Mug Risk", value=deal.mug_risk_reason, inline=False)
        embed.set_footer(text="Click title to open Torn Item Market • Manual buy only")

        await channel.send(content=pings or None, embed=embed)

    async def handle_notice(self, message: str) -> None:
        channel_id = self.watcher.alert_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(message)


bot = TornFlipperBot()


@bot.tree.command(name="help", description="Show all TornFlipper commands and how the faction market watcher works.")
async def help_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="💎 TornFlipper — Command Guide",
        description=(
            "Watches Torn's Item Market for underpriced listings across **all Torn items** "
            "and pings subscribed faction members for **0%-tax Bazaar flipping**."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🔑 API Key Pool (Private / Ephemeral)",
        value=(
            "`/setkey <api_key> [max_rpm]` — Save & activate your Torn API key (default 35 req/min, max 50)\n"
            "`/stopkey` — Immediately pause your key from being used\n"
            "`/startkey` — Re-activate your paused key\n"
            "`/removekey` — Delete your key from the bot"
        ),
        inline=False,
    )
    embed.add_field(
        name="📦 Watched Items",
        value=(
            "`/item add <item> [min_profit_pct] [max_price] [min_profit]` — Watch any Torn item\n"
            "`/item remove <item>` — Remove an item from the watch list\n"
            "`/item clear` — Clear all watched items\n"
            "`/item list` — View watched items, market values & target buy ceilings"
        ),
        inline=False,
    )
    embed.add_field(
        name="📡 Watcher, Presets & Pings",
        value=(
            "`/watch start` — Start watching in this channel using active pool keys\n"
            "`/watch stop` — Pause the market watcher\n"
            "`/watch status` — View watcher state, active/paused keys, and RPM usage\n"
            "`/watch preset <tier>` — Load starter high-liquidity presets (`tier_1`, `tier_2`, `tier_3`, `all`)\n"
            "`/catalog refresh` — Refresh Torn's item catalog & market values\n"
            "`/notify <True|False>` — Opt in/out of `@mention` pings on deal alerts"
        ),
        inline=False,
    )
    embed.set_footer(text="Rate-limit safe: per-key cap ≤ 50 req/min • Load-balanced across active keys • Manual buy only")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setkey", description="Register your Torn API key (private) with safe rate limiting.")
@app_commands.describe(
    api_key="Your Torn Public API key",
    max_rpm=f"Max requests/min for your key ({SAFE_MIN_RPM}-{SAFE_MAX_RPM}, default {DEFAULT_RPM})",
)
async def setkey(
    interaction: discord.Interaction,
    api_key: str,
    max_rpm: Optional[int] = None,
) -> None:
    await interaction.response.defer(ephemeral=True)
    ok, msg, profile = await bot.torn_api.verify_key(api_key.strip())
    if not ok or not profile:
        await interaction.followup.send(f"❌ Key verification failed: {msg}", ephemeral=True)
        return

    rpm = max_rpm if max_rpm is not None else DEFAULT_RPM
    entry = bot.key_pool.set_key(
        discord_id=interaction.user.id,
        torn_name=profile["name"],
        torn_id=profile["id"],
        api_key=api_key.strip(),
        max_rpm=rpm,
    )
    bot.watcher.set_subscriber(interaction.user.id, True)

    catalog_note = ""
    if bot.catalog.count() == 0:
        refreshed = await bot.catalog.refresh(force=True)
        if refreshed:
            catalog_note = f"\n📚 Cached **{bot.catalog.count():,}** Torn items for `/item add` autocomplete."

    await interaction.followup.send(
        f"✅ Saved & activated key for **{entry.torn_name} [{entry.torn_id}]** "
        f"(capped at **{entry.max_rpm} req/min** so you keep {100 - entry.max_rpm}+ req/min headroom).\n"
        f"You are also subscribed to faction flip pings (`/notify False` to mute).{catalog_note}",
        ephemeral=True,
    )


@bot.tree.command(name="stopkey", description="Immediately stop the bot from using your API key.")
async def stopkey(interaction: discord.Interaction) -> None:
    entry = bot.key_pool.pause_key(interaction.user.id, reason="Stopped by user")
    if not entry:
        await interaction.response.send_message("ℹ️ You don't have a registered API key.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"⏸️ Stopped using your API key (**{entry.torn_name}**). "
        f"Active pool keys remaining: **{bot.key_pool.active_count()}**.\n"
        f"Use `/startkey` anytime to re-enable it.",
        ephemeral=True,
    )


@bot.tree.command(name="startkey", description="Re-activate your stored Torn API key for the faction pool.")
async def startkey(interaction: discord.Interaction) -> None:
    entry = bot.key_pool.resume_key(interaction.user.id)
    if not entry:
        await interaction.response.send_message(
            "ℹ️ No stored key found. Use `/setkey` to register one.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"▶️ Re-activated your API key (**{entry.torn_name}**, cap **{entry.max_rpm} req/min**). "
        f"Active pool keys: **{bot.key_pool.active_count()}**.",
        ephemeral=True,
    )


@bot.tree.command(name="removekey", description="Delete your Torn API key from the bot completely.")
async def removekey(interaction: discord.Interaction) -> None:
    removed = bot.key_pool.remove_key(interaction.user.id)
    if not removed:
        await interaction.response.send_message("ℹ️ You don't have a stored API key.", ephemeral=True)
        return
    await interaction.response.send_message("🗑️ Your API key has been deleted.", ephemeral=True)


@bot.tree.command(name="notify", description="Toggle whether you get pinged when a flip deal is found.")
@app_commands.describe(enabled="True to receive @mention pings, False to mute")
async def notify(interaction: discord.Interaction, enabled: bool) -> None:
    bot.watcher.set_subscriber(interaction.user.id, enabled)
    state = "🔔 Subscribed to" if enabled else "🔕 Muted"
    await interaction.response.send_message(f"{state} faction flip deal pings.", ephemeral=True)


item_group = app_commands.Group(name="item", description="Manage watched Torn items for Bazaar flipping.")


async def catalog_item_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    matches = await bot.catalog.search_items(current, limit=25)
    return [
        app_commands.Choice(
            name=f"{it.name} (${it.market_value:,})"[:100],
            value=str(it.item_id),
        )
        for it in matches
    ]


async def watched_item_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    q = current.strip().lower()
    choices: list[app_commands.Choice[str]] = []
    for wi in bot.watcher.watched_items.values():
        if not q or q in wi.item_name.lower() or q in str(wi.item_id):
            choices.append(
                app_commands.Choice(
                    name=f"{wi.item_name} (#{wi.item_id})"[:100],
                    value=str(wi.item_id),
                )
            )
    return choices[:25]


@item_group.command(name="add", description="Add any Torn item to watch on the Item Market.")
@app_commands.autocomplete(item=catalog_item_autocomplete)
@app_commands.describe(
    item="Item name or Torn Item ID",
    min_profit_pct=f"Minimum Bazaar flip profit % below market value (default {DEFAULT_MIN_PROFIT_PCT}%)",
    max_price="Optional custom max buy price ($) overriding percentage target",
    min_profit="Optional minimum total Bazaar profit ($) per listing (default 0)",
)
async def item_add(
    interaction: discord.Interaction,
    item: str,
    min_profit_pct: float = DEFAULT_MIN_PROFIT_PCT,
    max_price: Optional[int] = None,
    min_profit: int = 0,
) -> None:
    await interaction.response.defer()
    cat_item = await bot.catalog.find_item(item)

    if cat_item:
        item_id = cat_item.item_id
        item_name = cat_item.name
        mv = cat_item.market_value
    elif item.strip().isdigit():
        item_id = int(item.strip())
        item_name = f"Item #{item_id}"
        mv = 0
    else:
        hint = (
            " Register an API key with `/setkey` first to load Torn's item catalog."
            if bot.catalog.count() == 0
            else ""
        )
        await interaction.followup.send(f"❌ Item `{item}` not found.{hint}")
        return

    wi = bot.watcher.add_item(
        item_id=item_id,
        item_name=item_name,
        min_profit_pct=max(0.0, min_profit_pct),
        max_price=max_price if max_price and max_price > 0 else None,
        min_profit=max(0, min_profit),
    )

    if wi.max_price is not None:
        ceiling_text = f"Custom Max Buy: **${wi.max_price:,}**"
    elif mv > 0:
        target = int(mv * (1.0 - wi.min_profit_pct / 100.0))
        ceiling_text = f"Market Value: **${mv:,}** | Alert ceiling: **${target:,}** (`-{wi.min_profit_pct:.1f}%`)"
    else:
        ceiling_text = f"Target: **-{wi.min_profit_pct:.1f}%** below live market average"

    min_profit_text = f" | Min Batch Profit: **${wi.min_profit:,}**" if wi.min_profit > 0 else ""
    await interaction.followup.send(
        f"✅ Added **{wi.item_name}** (`#{wi.item_id}`) — {ceiling_text}{min_profit_text}"
    )


@item_group.command(name="remove", description="Remove an item from the flip watch list.")
@app_commands.autocomplete(item=watched_item_autocomplete)
async def item_remove(interaction: discord.Interaction, item: str) -> None:
    target_id: Optional[int] = int(item) if item.strip().isdigit() else None
    if target_id is None:
        found = await bot.catalog.find_item(item)
        if found:
            target_id = found.item_id

    if target_id is None or target_id not in bot.watcher.watched_items:
        await interaction.response.send_message(f"ℹ️ `{item}` is not in the active watch list.")
        return

    removed_name = bot.watcher.watched_items[target_id].item_name
    bot.watcher.remove_item(target_id)
    await interaction.response.send_message(f"🗑️ Removed **{removed_name}** (`#{target_id}`) from the watch list.")


@item_group.command(name="clear", description="Clear all watched items.")
async def item_clear(interaction: discord.Interaction) -> None:
    count = bot.watcher.clear_items()
    await interaction.response.send_message(f"🧹 Cleared **{count}** watched item(s).")


@item_group.command(name="list", description="Show active watched items and their target buy ceilings.")
async def item_list(interaction: discord.Interaction) -> None:
    if not bot.watcher.watched_items:
        await interaction.response.send_message(
            "📭 No watched items set. Use `/item add` or `/watch preset` to add some."
        )
        return

    lines = []
    for wi in list(bot.watcher.watched_items.values())[:25]:
        cat_item = bot.catalog.get_item_sync(wi.item_id)
        mv = cat_item.market_value if cat_item else 0
        if wi.max_price is not None:
            target_str = f"Max Buy `${wi.max_price:,}`"
        elif mv > 0:
            target = int(mv * (1.0 - wi.min_profit_pct / 100.0))
            target_str = f"MV `${mv:,}` | Ceiling `${target:,}` (`-{wi.min_profit_pct:.1f}%`)"
        else:
            target_str = f"Target `-{wi.min_profit_pct:.1f}%`"
        lines.append(f"• **{wi.item_name}** (`#{wi.item_id}`) — {target_str}")

    extra = ""
    total = len(bot.watcher.watched_items)
    if total > 25:
        extra = f"\n…and **{total - 25}** more item(s)."

    embed = discord.Embed(
        title=f"📋 Watched Flip Items ({total})",
        description="\n".join(lines) + extra,
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed)


watch_group = app_commands.Group(name="watch", description="Start, stop, preset, or inspect the market watcher.")


@watch_group.command(name="start", description="Activate the market watcher in this channel using active pool keys.")
async def watch_start(interaction: discord.Interaction) -> None:
    bot.watcher.set_subscriber(interaction.user.id, True)
    if bot.key_pool.active_count() == 0:
        await interaction.response.send_message(
            "⚠️ No active API keys in the pool! At least one member needs `/setkey` or `/startkey` first.",
            ephemeral=True,
        )
        return
    if not bot.watcher.watched_items:
        await interaction.response.send_message(
            "⚠️ No items configured yet. Add items with `/item add` or `/watch preset`, then run `/watch start`."
        )
        return

    bot.watcher.start(interaction.channel_id)
    await interaction.response.send_message(
        f"🚀 **TornFlipper Watcher activated** in <#{interaction.channel_id}>!\n"
        f"• Active keys in pool: **{bot.key_pool.active_count()}** "
        f"(total safe capacity: **{bot.key_pool.total_rpm_capacity()} req/min**)\n"
        f"• Watching **{len(bot.watcher.watched_items)}** item(s) • Subscribers: **{len(bot.watcher.subscribers)}**"
    )


@watch_group.command(name="stop", description="Pause the market watcher for the faction.")
async def watch_stop(interaction: discord.Interaction) -> None:
    bot.watcher.stop()
    await interaction.response.send_message("🛑 **TornFlipper Watcher paused.** Any member can run `/watch start` to resume.")


@watch_group.command(name="preset", description="Load starter high-liquidity item presets (Plushies & Flowers).")
@app_commands.choices(
    tier=[
        app_commands.Choice(name="Tier 1 — High-value Plushies & Flowers (10 items)", value="tier_1"),
        app_commands.Choice(name="Tier 2 — Mid-value Plushies & Flowers (9 items)", value="tier_2"),
        app_commands.Choice(name="Tier 3 — Budget Flowers (2 items)", value="tier_3"),
        app_commands.Choice(name="All Tiers — All 21 Museum Plushies & Flowers", value="all"),
    ]
)
async def watch_preset(interaction: discord.Interaction, tier: str) -> None:
    presets = load_presets()
    if not presets:
        await interaction.response.send_message("❌ `presets.json` not found.")
        return

    selected: list[dict] = []
    if tier == "all":
        for key in ("tier_1", "tier_2", "tier_3"):
            selected.extend(presets.get(key, []))
    else:
        selected.extend(presets.get(tier, []))

    if not selected:
        await interaction.response.send_message(f"ℹ️ No preset items found for `{tier}`.")
        return

    for entry in selected:
        bot.watcher.add_item(
            item_id=int(entry["id"]),
            item_name=str(entry["name"]),
            min_profit_pct=float(entry.get("min_profit_pct", DEFAULT_MIN_PROFIT_PCT)),
            min_profit=int(entry.get("min_profit", 0)),
        )

    await interaction.response.send_message(
        f"⚡ Loaded **{len(selected)}** preset item(s) from `{tier}`. "
        f"Total watched items: **{len(bot.watcher.watched_items)}**."
    )


@watch_group.command(name="status", description="Show watcher state, active pool keys (no secrets), and RPM usage.")
async def watch_status(interaction: discord.Interaction) -> None:
    entries = bot.key_pool.list_entries()
    key_lines = []
    for e in entries:
        state_icon = "🟢 Active" if e.active else "⏸️ Paused"
        used = e.limiter.used_calls()
        key_lines.append(
            f"• **{e.torn_name}** (<@{e.discord_id}>): {state_icon} — `{used}/{e.limiter.max_calls} req/min` (total: {e.calls_made})"
        )

    embed = discord.Embed(
        title="📡 TornFlipper Watcher Status",
        color=discord.Color.green() if bot.watcher.watching else discord.Color.light_grey(),
    )
    embed.add_field(name="Watcher", value="🟢 Running" if bot.watcher.watching else "🛑 Stopped", inline=True)
    embed.add_field(name="Watched Items", value=str(len(bot.watcher.watched_items)), inline=True)
    embed.add_field(name="Subscribers", value=str(len(bot.watcher.subscribers)), inline=True)
    embed.add_field(name="Catalog Cached", value=f"{bot.catalog.count():,} items", inline=True)
    embed.add_field(
        name="API Key Pool",
        value="\n".join(key_lines) if key_lines else "No keys registered.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


catalog_group = app_commands.Group(name="catalog", description="Manage the cached Torn item catalog.")


@catalog_group.command(name="refresh", description="Refresh Torn's item catalog & market values using an active pool key.")
async def catalog_refresh(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    if bot.key_pool.active_count() == 0:
        await interaction.followup.send("⚠️ No active API keys in the pool. Register one with `/setkey` first.", ephemeral=True)
        return
    ok = await bot.catalog.refresh(force=True)
    if ok:
        await interaction.followup.send(
            f"✅ Refreshed Torn item catalog: **{bot.catalog.count():,}** items cached.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send("❌ Failed to refresh Torn item catalog.", ephemeral=True)


bot.tree.add_command(item_group)
bot.tree.add_command(watch_group)
bot.tree.add_command(catalog_group)


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and add your bot token.")
    bot.run(token)


if __name__ == "__main__":
    main()
