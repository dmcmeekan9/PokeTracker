from __future__ import annotations

from abc import ABC, abstractmethod

from poketracker.models import StockSignal, WatchlistItem


class SignalAdapter(ABC):
    @abstractmethod
    def check(self, item: WatchlistItem) -> StockSignal:
        raise NotImplementedError
