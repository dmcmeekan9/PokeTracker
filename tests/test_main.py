from __future__ import annotations

from poketracker import main as poketracker_main


def test_optional_positive_int_accepts_positive_value(monkeypatch) -> None:
    monkeypatch.setenv("POKETRACKER_BURST_DURATION_SECONDS", "600")

    assert poketracker_main._optional_positive_int("POKETRACKER_BURST_DURATION_SECONDS") == 600


def test_optional_positive_int_ignores_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("POKETRACKER_BURST_DURATION_SECONDS", "nope")

    assert poketracker_main._optional_positive_int("POKETRACKER_BURST_DURATION_SECONDS") is None


def test_burst_runs_until_duration_expires(monkeypatch) -> None:
    runs = []
    now = {"value": 0.0}

    def run_once() -> None:
        runs.append(now["value"])
        now["value"] += 1.0

    def monotonic() -> float:
        return now["value"]

    def sleep(seconds: float) -> None:
        now["value"] += seconds

    monkeypatch.setattr(poketracker_main, "run_once", run_once)
    monkeypatch.setattr(poketracker_main.time, "monotonic", monotonic)
    monkeypatch.setattr(poketracker_main.time, "sleep", sleep)

    poketracker_main._run_burst(duration_seconds=25, interval_seconds=10)

    assert runs == [0.0, 11.0, 22.0]
