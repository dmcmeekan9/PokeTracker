from __future__ import annotations

from poketracker.models import Decision


class DryRunCheckoutAdapter:
    def execute(self, decision: Decision) -> Decision:
        return decision
