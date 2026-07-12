# Target Checkout Ops Notes

## Add-to-cart failures

Current behavior:

- The monitor checks stock on its schedule.
- Target polling currently runs during the early-morning burst from about 1:55 AM to 4:05 AM Central.
- The burst is configured with a 5-second sleep, but a full iteration takes additional time; observed checks are about every 10-11 seconds.
- If an item is detected as buyable, it calls the checkout webhook.
- The checkout driver tries to add the item to cart.
- If Target says the item is in stock but the add-to-cart control is missing or will not click, checkout fails closed and records a purchase failure.
- The next monitor/burst iteration can try again if the item is still detected as buyable.
- The July 12, 2026 fix preserves webhook error statuses such as `target_add_to_cart_not_found`, then suppresses only expected Target stock-probe misses as `SKIP` so probe negatives do not send purchase-failure alerts.
- The checkout driver now clicks native buttons, ARIA buttons, and nearby clickable wrappers for `Add to cart`; this was required after the verify-only SKU rendered visible text without matching the old native-button selector.

Tonight's burst behavior:

- Burst starts at 1:55 AM Central.
- During the burst, checks are configured with a 5-second sleep and observed about every 10-11 seconds for 130 minutes.
- A July 7, 2026 miss at about 3:25 AM Central happened while checks were active; audit rows showed `html=out_of_stock redsky=unknown`.

Possible improvement:

- Treat add-to-cart failure as a short retryable condition inside the checkout webhook.
- For example: retry the product page/cart flow for 20-30 seconds before returning failure.
- That would avoid waiting for the next scheduled monitor run when Target briefly reports stock before the cart service is ready.

## Login, CAPTCHA, and session health

Current behavior:

- Checkout fails closed if Target asks for sign-in, CAPTCHA, MFA, CVV, or payment intervention.
- We validated the hosted EC2 Chrome session tonight and confirmed it can reach the final place-order button.
- There is no automatic recurring session health check currently enabled.

Recommended improvement:

- Add a scheduled verify-only health check before known drop windows.
- The health check should open Target account/cart or a low-risk in-stock item and confirm:
  - Target is still signed in.
  - No CAPTCHA or identity prompt is showing.
  - Saved shipping/payment are visible.
  - The checkout flow can reach `ready_to_place_order` without clicking Place Order.
- Alert immediately if the health check fails, so we can fix the EC2 browser session before a drop.

Good first schedule:

- Run a lightweight account/cart health check every 30-60 minutes.
- Run a deeper verify-only checkout proof at 1:45 AM and 2:45 AM Central.

Important note:

- A health check reduces risk but cannot guarantee Target will not present a fresh challenge during the actual purchase attempt.

## Workspace cleanup

- Generated test/debug artifacts should stay out of commits unless they are intentionally useful fixtures.
- When creating new temporary pytest, checkout, or debug output files, delete older generated files if they are no longer needed.
- Common cleanup targets include `.pytest_cache`, `pytest-cache-files-*`, and stale files under `.tmp`.
- Keep durable notes in this `docs/` file instead of leaving one-off scratch files at the repository root.

## Fast Production Check

On Windows, prefer the PowerShell check-infra script:

```powershell
pwsh -NoProfile -File scripts/validate-checkout.ps1
```

Expected success:

- EC2 Chrome instance is running.
- Lambda verify-only checkout returns `ready_to_place_order`.
- SSM cart cleanup succeeds and reports removed item count.

If Bash is available, `bash scripts/validate-checkout.sh` is equivalent.
