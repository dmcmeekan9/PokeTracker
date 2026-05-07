from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from poketracker.models import (
    Decision,
    DecisionType,
    GlobalConfig,
    SellerClassification,
    SignalStatus,
    StockSignal,
)


class RulesEngine:
    def __init__(self, global_config: GlobalConfig) -> None:
        self.global_config = global_config

    def evaluate(self, signal: StockSignal, weekly_spend_before: Decimal) -> Decision:
        item = signal.item
        quantity = item.max_quantity
        observed_price = signal.observed_price
        weekly_spend_after = weekly_spend_before

        if signal.status == SignalStatus.ERROR:
            return self._decision(signal, DecisionType.ERROR, signal.message or "signal error", weekly_spend_before, weekly_spend_after)

        if signal.status != SignalStatus.IN_STOCK:
            return self._decision(signal, DecisionType.SKIP, f"status is {signal.status.value}", weekly_spend_before, weekly_spend_after)

        if observed_price is None:
            return self._decision(signal, DecisionType.SKIP, "observed price unavailable", weekly_spend_before, weekly_spend_after)

        if observed_price > item.msrp:
            return self._decision(signal, DecisionType.SKIP, "observed price is above configured MSRP", weekly_spend_before, weekly_spend_after)

        candidate_spend = observed_price * quantity
        weekly_spend_after = weekly_spend_before + candidate_spend

        if signal.seller != SellerClassification.RETAILER:
            return self._decision(
                signal,
                DecisionType.FYI_ONLY,
                "price is at or below MSRP, but seller is third-party or unknown",
                weekly_spend_before,
                weekly_spend_after,
            )

        if weekly_spend_after > self.global_config.weekly_spend_cap:
            return self._decision(
                signal,
                DecisionType.SKIP,
                "weekly spend cap would be exceeded",
                weekly_spend_before,
                weekly_spend_after,
            )

        return self._decision(
            signal,
            DecisionType.WOULD_BUY,
            "dry-run would buy: price <= MSRP, seller is retailer, and weekly cap allows it",
            weekly_spend_before,
            weekly_spend_after,
        )

    @staticmethod
    def _decision(
        signal: StockSignal,
        decision_type: DecisionType,
        reason: str,
        weekly_spend_before: Decimal,
        weekly_spend_after: Decimal,
    ) -> Decision:
        return Decision(
            type=decision_type,
            item=signal.item,
            reason=reason,
            observed_price=signal.observed_price,
            msrp=signal.item.msrp,
            seller=signal.seller,
            quantity=signal.item.max_quantity,
            weekly_spend_before=weekly_spend_before,
            weekly_spend_after=weekly_spend_after,
            url=signal.item.url,
        )


def current_week_start_iso(timezone_name: str, now: datetime | None = None) -> str:
    tz = ZoneInfo(timezone_name)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    week_start = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return week_start.isoformat()
