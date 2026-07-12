# AI Agent Handoff

Use the local Codex skill `poketracker-ops` for future PokeTracker production checkout work.

Fresh-chat prompt:

```text
Use $poketracker-ops. We are in C:\git\PokeTracker. For production readiness, run pwsh -NoProfile -File scripts/validate-checkout.ps1, then inspect only the failing area.
```

The skill is installed at:

```text
C:\Users\denni\.codex\skills\poketracker-ops
```

Start points:

- `docs/target-checkout-ops.md` for tonight's operational notes and tomorrow's backlog.
- `watchlist.yaml` for item quantity/MSRP/watchlist configuration.
- `src/poketracker/checkout_webhook/target_driver.py` for Target checkout behavior.
- `src/poketracker/main.py` for polling and burst behavior.
- `infra/main/main.tf` and `.github/workflows/deploy.yml` for production deploy wiring.

Standing rules:

- Validate with `verify_only=true` before any real purchase-path change.
- Clear rehearsal items from the active Target cart after validation.
- On Windows, use `scripts/validate-checkout.ps1` instead of the Bash check-infra script.
- July 12, 2026: expected Target stock-probe misses are suppressed as `SKIP`; real checkout failures still alert.
- Check `git status --short` before editing or staging.
- Do not stage unrelated `infra/main/main.tf` changes casually; user-data changes can restart/replace the checkout browser.
- Delete stale generated artifacts such as `.pytest_cache`, `pytest-cache-files-*`, and unneeded `.tmp` files.
