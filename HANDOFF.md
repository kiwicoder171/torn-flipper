# TornFlipper — Faction Bazaar & Market Flipper Discord Bot (Handoff)

## 1. Goal & Context
Build **TornFlipper**, a standalone, 100% Torn-rule-compliant **Discord Bot** for a Torn faction. Unlike the archived webhook-only daemon in `../archive/TornFlipper/` and the TFC-focused `../TornQuestBot/`, this bot lets faction members:
- Privately contribute & manage their own Torn Public API keys (`/setkey`, `/stopkey`, `/startkey`, `/removekey`).
- Watch any Torn items (Xanax, E-DVDs, Energy Cans, Plushies, Flowers, faction supplies, etc.) on the Item Market.
- Receive shared Discord alerts whenever a listing is priced profitably for **0%-tax Bazaar flipping** (or below a custom target price / minimum profit margin).

---

## 2. Reference Code to Reuse
1. **From `../TornQuestBot/`** (multi-user bot & rate-limit safety):
   - `rate_limiter.py`: Per-key `SlidingWindowRateLimiter` (default **35 req/min**, hard-clamped **5–50 req/min**, smooth metronome spacing).
   - `key_pool.py`: `KeyPool` with per-user `active` toggle, capacity-aware round-robin load balancing, and `0600` JSON persistence.
   - `torn_api.py`: Torn API v2 client (`/v2/user/?selections=basic`, `/v2/market/{item_id}/itemmarket`) with HTTP 429 / Error 5 dynamic throttling and Error 1/2/10/18 auto-pause.
   - `bot.py`: Slash command patterns (`/help`, `/setkey`, `/stopkey`, `/startkey`, `/removekey`, `/notify`, `/watch start|stop|status`).
2. **From `../archive/TornFlipper/`** (flipping math & presets):
   - `analyzer.py`: Dual-venue profit calculation (**0% Bazaar resale** vs **5% Item Market fee**), minimum profit % / flat profit threshold, and outlier/mugger-trap guards.
   - `items.json`: Starter high-liquidity item presets (Tier 1/2/3).

---

## 3. Key Differences from `TornQuestBot`
- **All Torn Items (Not TFC-only)**: Cache Torn's item catalog (`/v2/torn/items` + `market_value`) for slash-command autocomplete and baseline pricing instead of `tornflight.club/api/items/`.
- **Bazaar Flip Math**:
  - Default target: `listed_price <= market_value * (1 - min_profit_pct / 100)` (with 0% Bazaar seller fee).
  - Embeds show: Listed Price, Torn Market Value, **Bazaar Flip Profit (0% fee)**, **Item Market Flip Profit (5% fee)**, Quantity, Total Profit, and 1-click Item Market link.
- **Presets**: Optional `/watch preset <tier>` or `/item add <item> [min_profit_pct | max_price]` for quick faction setup.
- **No TFC Fields**: Remove `TFC Recipient`, `Daily NFS`, and `/quest nfs`.

---

## 4. Deployment & Infrastructure
- **Git**: Initialize `TornFlipper/` as its own standalone Git repo (e.g., `kiwicoder171/torn-flipper`) with `.gitignore` excluding `.env`, `.venv/`, `data/`, `__pycache__/`.
- **Hosting (Shared Azure VM)**: Deploy alongside `TornQuestBot` on the existing Azure for Students VM (`torn-quest-bot`, resource group `torn-quest-rg`, region `eastasia`):
  - Directory: `/home/azureuser/torn-flipper`
  - Isolated `.venv` + `.env` (`DISCORD_BOT_TOKEN=...`, `chmod 600`)
  - Dedicated `systemd` unit: `/etc/systemd/system/tornflipper.service`
  - Deploy via `az vm run-command invoke` in Azure Cloud Shell (port 22 SSH is filtered from local Mac network).

---

## 5. Torn Rule Compliance
- **Alert-only**: Never auto-buys, auto-lists, or automates browser actions.
- **Official API v2 only**: Strictly respects rate limits (`<= 50 req/min` per key).
