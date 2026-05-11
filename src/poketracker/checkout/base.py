from __future__ import annotations

from abc import ABC, abstractmethod

from poketracker.models import Decision


class CheckoutAdapter(ABC):
    @abstractmethod
    def execute(self, decision: Decision) -> Decision:
        raise NotImplementedError
