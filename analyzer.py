import urllib.parse
from dataclasses import dataclass
from typing import Any, Optional


ITEM_MARKET_FEE_RATE = 0.05


@dataclass(frozen=True)
class FlipDeal:
    item_id: int
    item_name: str
    listed_price: int
    amount: int
    total_cost: int
    market_value: int
    target_price: int
    discount_pct: float
    bazaar_profit_per_unit: int
    bazaar_total_profit: int
    imarket_fee_per_unit: int
    imarket_profit_per_unit: int
    imarket_total_profit: int
    is_high_mug_risk: bool
    mug_risk_reason: Optional[str]
    market_url: str


def evaluate_flip_listings(
    item_id: int,
    item_name: str,
    market_value: int,
    market_payload: dict[str, Any],
    min_profit_pct: float = 3.0,
    max_price: Optional[int] = None,
    min_profit: int = 0,
    enable_mug_risk_warning: bool = True,
) -> list[FlipDeal]:
    """
    Evaluate Torn API v2 Item Market listings for 0%-tax Bazaar flipping.

    Default target: listed_price <= market_value * (1 - min_profit_pct / 100).
    If max_price is specified (> 0), listed_price <= max_price is used as the ceiling.
    Also computes 5%-tax Item Market resale profit for comparison and flags mugger traps.
    """
    imarket = market_payload.get("itemmarket") or {}
    v2_item = imarket.get("item") or {}
    resolved_name = str(v2_item.get("name") or item_name)
    effective_market_value = market_value if market_value > 0 else int(v2_item.get("average_price") or 0)

    if effective_market_value <= 0:
        return []

    if max_price is not None and max_price > 0:
        target_price = int(max_price)
    else:
        target_price = int(effective_market_value * (1.0 - min_profit_pct / 100.0))

    if target_price <= 0:
        return []

    listings = imarket.get("listings") or []
    encoded_name = urllib.parse.quote(resolved_name)
    market_url = (
        f"https://www.torn.com/page.php?sid=ItemMarket#/market/view=search"
        f"&itemID={item_id}&itemName={encoded_name}"
    )

    deals: list[FlipDeal] = []
    imarket_fee_per_unit = int(effective_market_value * ITEM_MARKET_FEE_RATE)

    for listing in listings:
        price = int(listing.get("price") or 0)
        amount = int(listing.get("amount") or 0)
        if price <= 0 or amount <= 0:
            continue
        if price > target_price:
            continue

        bazaar_profit_per_unit = effective_market_value - price
        if max_price is None and bazaar_profit_per_unit <= 0:
            continue

        bazaar_total_profit = bazaar_profit_per_unit * amount
        if min_profit > 0 and bazaar_total_profit < min_profit:
            continue

        imarket_profit_per_unit = (effective_market_value - imarket_fee_per_unit) - price
        imarket_total_profit = imarket_profit_per_unit * amount
        discount_pct = ((effective_market_value - price) / effective_market_value) * 100.0

        is_mug_risk = False
        mug_reason = None
        if enable_mug_risk_warning and amount <= 3 and discount_pct >= 5.0:
            is_mug_risk = True
            mug_reason = (
                f"Low qty ({amount} unit{'s' if amount > 1 else ''}) "
                f"at {discount_pct:.1f}% below market — possible mugger trap"
            )

        deals.append(
            FlipDeal(
                item_id=item_id,
                item_name=resolved_name,
                listed_price=price,
                amount=amount,
                total_cost=price * amount,
                market_value=effective_market_value,
                target_price=target_price,
                discount_pct=discount_pct,
                bazaar_profit_per_unit=bazaar_profit_per_unit,
                bazaar_total_profit=bazaar_total_profit,
                imarket_fee_per_unit=imarket_fee_per_unit,
                imarket_profit_per_unit=imarket_profit_per_unit,
                imarket_total_profit=imarket_total_profit,
                is_high_mug_risk=is_mug_risk,
                mug_risk_reason=mug_reason,
                market_url=market_url,
            )
        )

    deals.sort(key=lambda d: d.listed_price)
    return deals
