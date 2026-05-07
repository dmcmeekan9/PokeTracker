from __future__ import annotations

from decimal import Decimal

from poketracker.models import (
    Decision,
    DecisionType,
    ProductType,
    Retailer,
    SellerClassification,
    WatchlistItem,
)
from poketracker.notify.email import render_decision_email


def test_render_would_buy_email() -> None:
    decision = Decision(
        type=DecisionType.WOULD_BUY,
        item=WatchlistItem(
            id="target-ascended-heroes-etb",
            name="Target: Ascended Heroes Elite Trainer Box",
            retailer=Retailer.TARGET,
            url="https://www.target.com/p/example/-/A-95082118",
            type=ProductType.ETB,
            msrp=Decimal("59.99"),
            max_quantity=1,
            enabled=True,
            sku="95082118",
        ),
        reason="dry-run would buy",
        observed_price=Decimal("59.99"),
        msrp=Decimal("59.99"),
        seller=SellerClassification.RETAILER,
        quantity=1,
        weekly_spend_before=Decimal("0"),
        weekly_spend_after=Decimal("59.99"),
        url="https://www.target.com/p/example/-/A-95082118",
    )

    subject, body = render_decision_email(decision, subject_prefix="[TEST]")

    assert subject == "[TEST] PokeTracker WOULD_BUY: Target: Ascended Heroes Elite Trainer Box"
    assert "Decision: WOULD_BUY" in body
    assert "Observed price: $59.99" in body
    assert "Seller classification: retailer" in body
    assert "Weekly spend after: $59.99" in body
    assert body.splitlines()[2] == "URL: https://www.target.com/p/example/-/A-95082118"
