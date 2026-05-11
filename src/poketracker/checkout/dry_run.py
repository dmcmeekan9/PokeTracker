from __future__ import annotations

from dataclasses import replace

from poketracker.checkout.base import CheckoutAdapter
from poketracker.models import Decision
from poketracker.models import DecisionType


class DryRunCheckoutAdapter(CheckoutAdapter):
    def execute(self, decision: Decision) -> Decision:
        return decision


class UnconfiguredCheckoutAdapter(CheckoutAdapter):
    def execute(self, decision: Decision) -> Decision:
        if decision.type != DecisionType.WOULD_BUY:
            return decision
        return replace(
            decision,
            type=DecisionType.PURCHASE_FAILED,
            reason="purchasing is enabled but CHECKOUT_WEBHOOK_URL is not configured",
            weekly_spend_after=decision.weekly_spend_before,
            checkout_status="unconfigured",
            checkout_message="Set CHECKOUT_WEBHOOK_URL to submit v2 purchase requests.",
        )
