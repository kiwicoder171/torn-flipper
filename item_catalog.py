import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from key_pool import KeyPool
from torn_api import TornApiClient

logger = logging.getLogger("TornFlipper.Catalog")


@dataclass(frozen=True)
class TornItem:
    item_id: int
    name: str
    market_value: int
    item_type: str = "Unknown"
    tradeable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "market_value": self.market_value,
            "item_type": self.item_type,
            "tradeable": self.tradeable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TornItem":
        return cls(
            item_id=int(data["item_id"]),
            name=str(data["name"]),
            market_value=int(data.get("market_value", 0)),
            item_type=str(data.get("item_type", "Unknown")),
            tradeable=bool(data.get("tradeable", True)),
        )


def parse_torn_items_payload(payload: dict[str, Any]) -> dict[int, TornItem]:
    """
    Parse Torn API v2 /v2/torn/items response into {item_id: TornItem}.
    Handles both list-based v2 responses and dict-based legacy responses.
    """
    raw_items = payload.get("items") or {}
    parsed: dict[int, TornItem] = {}

    if isinstance(raw_items, list):
        entries = raw_items
    elif isinstance(raw_items, dict):
        entries = []
        for k, v in raw_items.items():
            if isinstance(v, dict):
                item_copy = dict(v)
                item_copy.setdefault("id", int(k))
                entries.append(item_copy)
    else:
        return parsed

    for entry in entries:
        try:
            item_id = int(entry.get("id") or 0)
            name = str(entry.get("name") or "").strip()
            if item_id <= 0 or not name:
                continue

            value_obj = entry.get("value")
            if isinstance(value_obj, dict):
                mv = int(value_obj.get("market_price") or value_obj.get("market_value") or 0)
            else:
                mv = int(entry.get("market_value") or entry.get("market_price") or 0)

            item_type = str(entry.get("type") or "Unknown")
            tradeable_raw = entry.get("is_tradable", entry.get("tradeable", True))
            tradeable = bool(tradeable_raw)

            parsed[item_id] = TornItem(
                item_id=item_id,
                name=name,
                market_value=mv,
                item_type=item_type,
                tradeable=tradeable,
            )
        except (TypeError, ValueError):
            continue

    return parsed


class ItemCatalog:
    """
    Caches Torn's full item catalog (/v2/torn/items) with market_value baselines
    for slash-command autocomplete and Bazaar flip analysis.
    """

    def __init__(
        self,
        torn_api: Optional[TornApiClient] = None,
        key_pool: Optional[KeyPool] = None,
        storage_path: Optional[Path] = None,
        cache_ttl_seconds: float = 3600.0,
    ):
        self.torn_api = torn_api
        self.key_pool = key_pool
        self.storage_path = storage_path
        self.cache_ttl_seconds = cache_ttl_seconds
        self._items: dict[int, TornItem] = {}
        self._last_refresh_wall: float = 0.0
        self._lock = asyncio.Lock()

        if self.storage_path and self.storage_path.exists():
            self._load()

    def count(self) -> int:
        return len(self._items)

    def is_stale(self) -> bool:
        if not self._items:
            return True
        return (time.time() - self._last_refresh_wall) >= self.cache_ttl_seconds

    def seed_items(self, items: list[TornItem]) -> None:
        """Directly seed items into the catalog (useful for presets or tests)."""
        for item in items:
            self._items[item.item_id] = item
        if self._last_refresh_wall == 0.0:
            self._last_refresh_wall = time.time()

    async def refresh(self, force: bool = False) -> bool:
        """Refresh the item catalog from Torn API v2 using an active pool key."""
        if not force and not self.is_stale():
            return True
        if not self.torn_api or not self.key_pool:
            return False

        async with self._lock:
            if not force and not self.is_stale():
                return True
            key_entry = await self.key_pool.acquire_next_key()
            if not key_entry:
                return False
            try:
                payload = await self.torn_api.fetch_item_catalog(key_entry)
                parsed = parse_torn_items_payload(payload)
                if parsed:
                    self._items = parsed
                    self._last_refresh_wall = time.time()
                    self._save()
                    logger.info("Refreshed Torn item catalog: %d items cached.", len(self._items))
                    return True
            except Exception as exc:
                logger.warning("Failed to refresh Torn item catalog: %s", exc)
            return False

    async def get_item(self, item_id: int) -> Optional[TornItem]:
        if self.is_stale():
            await self.refresh(force=False)
        return self._items.get(item_id)

    def get_item_sync(self, item_id: int) -> Optional[TornItem]:
        return self._items.get(item_id)

    async def search_items(self, query: str, limit: int = 25) -> list[TornItem]:
        """Search cached items for Discord slash-command autocomplete."""
        q = query.strip().lower()
        items = list(self._items.values())

        if not q:
            # Show popular/high-value tradeable items first
            tradeable = [it for it in items if it.tradeable and it.market_value > 0]
            tradeable.sort(key=lambda it: it.name)
            return tradeable[:limit]

        prefix_matches: list[TornItem] = []
        substring_matches: list[TornItem] = []

        for it in items:
            if not it.tradeable:
                continue
            name_l = it.name.lower()
            id_str = str(it.item_id)
            if id_str == q or name_l.startswith(q):
                prefix_matches.append(it)
            elif q in name_l or q in id_str:
                substring_matches.append(it)

        prefix_matches.sort(key=lambda it: (it.name,))
        substring_matches.sort(key=lambda it: (it.name,))
        return (prefix_matches + substring_matches)[:limit]

    async def find_item(self, query: str) -> Optional[TornItem]:
        """Resolve an item by numeric ID or exact/partial name."""
        if self.is_stale():
            await self.refresh(force=False)

        q = query.strip()
        if not q:
            return None

        if q.isdigit():
            by_id = self._items.get(int(q))
            if by_id:
                return by_id

        q_lower = q.lower()
        for it in self._items.values():
            if it.name.lower() == q_lower:
                return it

        matches = await self.search_items(q, limit=1)
        return matches[0] if matches else None

    def _load(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self._last_refresh_wall = float(raw.get("updated_at", 0.0))
            for entry in raw.get("items", []):
                item = TornItem.from_dict(entry)
                self._items[item.item_id] = item
        except Exception:
            pass

    def _save(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": self._last_refresh_wall,
            "items": [it.to_dict() for it in self._items.values()],
        }
        self.storage_path.write_text(json.dumps(payload), encoding="utf-8")
