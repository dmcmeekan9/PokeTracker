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

    subject, body, html_body = render_decision_email(
        decision,
        subject_prefix="[TEST]",
        footer_gif_url="https://example.com/footer.gif",
    )

    assert subject == "[TEST] PokeTracker BUY THIS! Target: Ascended Heroes Elite Trainer Box"
    assert "Decision: BUY THIS!" in body
    assert "Observed price: $59.99" in body
    assert "Seller classification: retailer" in body
    assert "Weekly spend after: $59.99" in body
    assert body.splitlines()[2] == "URL: https://www.target.com/p/example/-/A-95082118"
    assert "<strong>Reason:</strong> dry-run would buy" in html_body
    assert ">BUY THIS!</div>" in html_body
    assert "WOULD_BUY" not in html_body
    assert "color:#5f6368;font-weight:700;width:180px" in html_body
    assert "border:1px solid #edf0f3" in html_body
    assert "background:#f1efff" not in html_body
    assert "border-right:1px solid #ded8ff" not in html_body
    assert "PokeTracker alert for" not in html_body
    assert "font-size:16px;line-height:1.35;font-weight:700;margin-top:8px" not in html_body
    assert "Open Target Page" in html_body
    assert html_body.count("https://www.target.com/p/example/-/A-95082118") == 1
    assert "https://example.com/footer.gif" in html_body
