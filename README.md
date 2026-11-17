> 🧠 **Obsidian Hub**: [[_Brain/Projects/Torn|Torn]] · [[Antigravity/Torn/CONTEXT|Torn/CONTEXT.md]]

# TornFlipper — Faction Bazaar & Market Flipper Discord Bot

A 100% Torn-rule-compliant Discord bot that lets faction members pool their Torn Public API keys safely, watch any Torn items on the Item Market, and receive shared alerts whenever listings are priced profitably for **0%-tax Bazaar flipping**.

## Key Features
- **Per-User Rate-Limit Protection**: Each registered API key is capped by default at **35 req/min** (configurable `5–50 req/min`, hard-capped at 50) with smooth metronome spacing and automatic HTTP 429 / Error 5 throttling — leaving 50–65+ req/min headroom under Torn's 100 req/min account limit.
- **Multi-User Key Pool & Instant Pause/Resume**:
  - Members register keys privately via `/setkey` (ephemeral, stored with `0600` permissions).
  - Anyone can pause their own key anytime with `/stopkey` or re-enable it with `/startkey`.
  - Load-balances requests across active keys.
- **Full Torn Item Catalog & Bazaar Flip Math**:
  - Caches `/v2/torn/items` (`market_value`) for slash-command autocomplete and baseline pricing.
  - Compares **Bazaar Flip Profit (0% seller fee)** vs **Item Market Flip Profit (5% seller fee)** and flags low-quantity deep-discount mugger traps.
  - Includes starter high-liquidity Plushie & Flower presets (`/watch preset`).

## Setup
```bash
cd TornFlipper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add your DISCORD_BOT_TOKEN to .env
python3 bot.py
```

## Slash Commands
| Command | Description |
| :--- | :--- |
| `/setkey <api_key> [max_rpm]` | Privately register & activate your Torn API key (default 35 req/min). |
| `/stopkey` | Immediately stop the bot from using your API key. |
| `/startkey` | Re-activate your stored API key. |
| `/removekey` | Delete your API key from the bot. |
| `/item add <item> [min_profit_pct] [max_price] [min_profit]` | Watch any Torn item (with catalog autocomplete). |
| `/item remove <item>` | Remove an item from the watch list. |
| `/item clear` | Clear all watched items. |
| `/item list` | View watched items, market values, and target buy ceilings. |
| `/watch start` | Start watching in the current channel using active pool keys. |
| `/watch stop` | Pause the market watcher. |
| `/watch status` | Inspect watcher status, active/paused keys, and RPM usage. |
| `/watch preset <tier>` | Load starter presets (`tier_1`, `tier_2`, `tier_3`, `all`). |
| `/catalog refresh` | Refresh Torn's item catalog & market values. |
| `/notify <True\|False>` | Opt in or out of `@mention` pings on deal alerts. |
