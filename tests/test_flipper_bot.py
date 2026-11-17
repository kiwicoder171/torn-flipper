import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer import evaluate_flip_listings
from item_catalog import ItemCatalog, TornItem, parse_torn_items_payload
from key_pool import KeyPool, SAFE_MAX_RPM, SAFE_MIN_RPM
from torn_api import TornApiException
from watcher import FlipWatcher


def test_key_pool_clamps_rpm_and_toggles(tmp_path):
    storage = tmp_path / "keys.json"
    pool = KeyPool(storage_path=storage)

    e1 = pool.set_key(101, "Alice", 1001, "KEY_A", max_rpm=120)
    assert e1.max_rpm == SAFE_MAX_RPM
    assert e1.active is True

    e2 = pool.set_key(202, "Bob", 1002, "KEY_B", max_rpm=1)
    assert e2.max_rpm == SAFE_MIN_RPM

    pool.pause_key(101)
    assert pool.active_count() == 1
    assert [e.discord_id for e in pool.active_entries()] == [202]

    reloaded = KeyPool(storage_path=storage)
    assert reloaded.get_entry(101).active is False
    assert reloaded.get_entry(202).active is True

    reloaded.resume_key(101)
    assert reloaded.active_count() == 2
    assert reloaded.remove_key(202) is True
    assert reloaded.active_count() == 1


def test_key_pool_load_balances_across_active_keys():
    async def run():
        pool = KeyPool()
        pool.set_key(1, "Alice", 11, "KEY_1", max_rpm=30)
        pool.set_key(2, "Bob", 22, "KEY_2", max_rpm=30)

        k1 = await pool.acquire_next_key()
        k2 = await pool.acquire_next_key()
        assert k1 is not None and k2 is not None
        assert {k1.discord_id, k2.discord_id} == {1, 2}

        pool.pause_key(1)
        k3 = await pool.acquire_next_key()
        assert k3 is not None and k3.discord_id == 2

    asyncio.run(run())


def test_item_catalog_parse_search_and_find(tmp_path):
    payload = {
        "items": [
            {
                "id": 206,
                "name": "Xanax",
                "type": "Drug",
                "value": {"market_price": 840000},
                "is_tradable": True,
            },
            {
                "id": 366,
                "name": "Erotic DVD",
                "type": "Booster",
                "value": {"market_price": 4500000},
                "is_tradable": True,
            },
            {
                "id": 999,
                "name": "Untradable Token",
                "type": "Special",
                "value": {"market_price": 100},
                "is_tradable": False,
            },
        ]
    }
    parsed = parse_torn_items_payload(payload)
    assert set(parsed.keys()) == {206, 366, 999}
    assert parsed[206].market_value == 840000

    async def run():
        cat = ItemCatalog(storage_path=tmp_path / "catalog.json")
        cat.seed_items(list(parsed.values()))
        matches = await cat.search_items("xan")
        assert [m.item_id for m in matches] == [206]

        # Untradable items are excluded from autocomplete search
        untradable = await cat.search_items("Untradable")
        assert untradable == []

        by_id = await cat.find_item("366")
        assert by_id is not None and by_id.name == "Erotic DVD"

    asyncio.run(run())


def test_evaluate_flip_listings_bazaar_vs_imarket_and_mug_risk():
    payload = {
        "itemmarket": {
            "item": {"name": "Xanax", "average_price": 840000},
            "listings": [
                {"price": 830000, "amount": 10},  # Only 1.2% off -> below 3% target (814,800)
                {"price": 810000, "amount": 10},  # 3.57% off -> valid, no mug risk (qty 10)
                {"price": 750000, "amount": 2},   # 10.7% off, qty 2 -> valid + mug risk warning
            ],
        }
    }

    deals = evaluate_flip_listings(
        item_id=206,
        item_name="Xanax",
        market_value=840000,
        market_payload=payload,
        min_profit_pct=3.0,
    )
    assert [d.listed_price for d in deals] == [750000, 810000]

    mug_deal = deals[0]
    assert mug_deal.is_high_mug_risk is True
    assert mug_deal.mug_risk_reason is not None
    assert mug_deal.bazaar_profit_per_unit == 840000 - 750000
    assert mug_deal.imarket_fee_per_unit == int(840000 * 0.05)
    assert mug_deal.imarket_profit_per_unit == (840000 - 42000) - 750000

    safe_deal = deals[1]
    assert safe_deal.is_high_mug_risk is False
    assert safe_deal.bazaar_profit_per_unit == 30000
    assert safe_deal.bazaar_total_profit == 300000
    # At 810k buy vs 840k MV, 5% Item Market fee (42k) makes Item Market resale -12k/ea while Bazaar is +30k/ea
    assert safe_deal.imarket_profit_per_unit == -12000


def test_evaluate_flip_listings_custom_max_price_and_min_profit():
    payload = {
        "itemmarket": {
            "listings": [
                {"price": 50000, "amount": 1},  # Total profit 3k -> filtered by min_profit=10k
                {"price": 50000, "amount": 5},  # Total profit 15k -> passes
            ]
        }
    }
    deals = evaluate_flip_listings(
        item_id=281,
        item_name="Lion Plushie",
        market_value=53000,
        market_payload=payload,
        max_price=50000,
        min_profit=10000,
    )
    assert len(deals) == 1
    assert deals[0].amount == 5
    assert deals[0].bazaar_total_profit == 15000


class FakeTornApi:
    INVALID_KEY_CODES = {1, 2, 10, 18}

    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error

    async def fetch_item_market(self, item_id: int, key_entry):
        if self.error:
            if self.error.code in self.INVALID_KEY_CODES:
                key_entry.active = False
            raise self.error
        return self.payload


def test_watcher_scan_once_deduplicates_and_notifies():
    async def run():
        pool = KeyPool()
        pool.set_key(1, "Alice", 11, "KEY_1", max_rpm=35)
        cat = ItemCatalog()
        cat.seed_items([TornItem(item_id=206, name="Xanax", market_value=840000)])

        payload = {"itemmarket": {"listings": [{"price": 800000, "amount": 5}]}}
        deals_sent = []

        async def on_deal(deal):
            deals_sent.append(deal)

        watcher = FlipWatcher(
            key_pool=pool,
            catalog=cat,
            torn_api=FakeTornApi(payload=payload),
            on_deal=on_deal,
        )
        watcher.add_item(206, "Xanax", min_profit_pct=3.0)

        first = await watcher.scan_once()
        assert len(first) == 1
        assert len(deals_sent) == 1
        assert deals_sent[0].bazaar_profit_per_unit == 40000

        second = await watcher.scan_once()
        assert len(second) == 0
        assert len(deals_sent) == 1

    asyncio.run(run())


def test_watcher_auto_pauses_invalid_key_and_notifies():
    async def run():
        pool = KeyPool()
        pool.set_key(1, "Alice", 11, "BAD_KEY", max_rpm=35)
        cat = ItemCatalog()
        cat.seed_items([TornItem(item_id=206, name="Xanax", market_value=840000)])
        notices = []

        async def on_notice(msg: str):
            notices.append(msg)

        watcher = FlipWatcher(
            key_pool=pool,
            catalog=cat,
            torn_api=FakeTornApi(error=TornApiException(2, "Incorrect key")),
            on_notice=on_notice,
        )
        watcher.add_item(206, "Xanax")

        await watcher.scan_once()
        assert pool.active_count() == 0
        assert len(notices) == 1
        assert "Auto-paused" in notices[0]

    asyncio.run(run())


def test_presets_json_valid():
    presets_path = Path(__file__).resolve().parents[1] / "presets.json"
    data = json.loads(presets_path.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"tier_1", "tier_2", "tier_3"}
    total = sum(len(v) for v in data.values())
    assert total == 21
