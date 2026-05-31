# PokeTracker

## Purpose
Automated AWS bot that monitors Target.com (and soon other retailers) for Pokemon card restocks and purchases at MSRP during nightly burst windows. Uses a persistent EC2-hosted Chrome session + Playwright Lambda for unattended checkout.

## Architecture
- **ECS Fargate** — monitor task, runs every minute; burst windows at 1:55 AM and 2:55 AM CT (10 min each, 10s interval)
- **Lambda `poketracker-prod-checkout-webhook`** — receives purchase requests, drives Target checkout over CDP; 300s timeout
- **EC2 Chrome** (tag: `poketracker-prod-target-checkout-browser`) — persistent Chrome with saved Target session; CDP on VPC-private port 9223 → socat → 9222; VNC on 5901
- **EventBridge** — triggers Fargate monitor task and burst windows
- **DynamoDB** — audit log, state, weekly spend tracking
- **SES** — purchase alerts and failure notifications
- **Secrets Manager** — Target session, checkout profile, webhook token, Target credentials

## Common Tasks

### Validate infra (run before drop windows)
```bash
bash scripts/validate-checkout.sh
```
Runs verify-only Lambda checkout (~90s), confirms EC2 Chrome is live, Target session is valid, cart is cleaned up. Use `/check-infra` skill. Always uses `verify_only: true`.

### Run tests
```bash
uv run pytest
```

### Deploy to AWS
Push to `main` branch or trigger GitHub Actions manually. The validate workflow runs first, then builds/pushes Docker, applies Terraform, syncs DynamoDB watchlist. Use `/deploy` skill.

### Refresh Target session (automated — runs nightly at 1:25 AM CT)
The `poketracker-prod-target-session-refresh` Lambda auto-signs-in to EC2 Chrome using credentials from Secrets Manager and saves the refreshed session. Runs automatically before each burst window via EventBridge schedule.

To trigger manually (e.g. if check-infra reports sign-in failures):
```bash
aws lambda invoke --function-name poketracker-prod-target-session-refresh \
  --cli-binary-format raw-in-base64-out --payload '{}' --region us-east-1 /tmp/r.json \
  && cat /tmp/r.json
```

### Re-warm EC2 Chrome tabs (run after session refresh)
```bash
aws lambda invoke --function-name poketracker-prod-tab-warmer \
  --cli-binary-format raw-in-base64-out --payload '{}' --region us-east-1 /tmp/w.json \
  && cat /tmp/w.json
```

### Recovery sequence when check-infra fails (sign-in / session issues)
1. Invoke session refresh Lambda (signs in via stored credentials)
2. Invoke tab warmer Lambda
3. Re-run `/check-infra`

### VNC into EC2 Chrome (last resort — has never been needed in practice)
Use `/vnc` skill. SSM port-forward to 5901, connect VNC client to `127.0.0.1:5901`. Only relevant if Target ever triggers a CAPTCHA or MFA challenge, which has not occurred. The nightly refresh keeps the session alive; if it ever expires, auto-sign-in handles recovery.

### Local verify checkout
```powershell
$env:PYTHONPATH = "src"
uv run poketracker-verify-target-checkout --url "https://www.target.com/p/spaghettios-original-pok-233-mon-shapes-canned-pasta-15-8oz/-/A-95042532" --item-name "Test"
```

## Safety Rules
- **Always `verify_only: true`** in any test Lambda invocation — prod Lambda has `TARGET_PLACE_ORDER_ENABLED: true`
- **Never hardcode EC2 instance ID** — always look up by tag `poketracker-prod-target-checkout-browser`
- **Never inspect EC2 Chrome via CDP WebSocket scripts** (SSM Python/websocket scripts that connect to Chrome's debug port) — these destabilize the CDP session and cause Lambda `Browser.new_context` / `Target page...closed` failures. Use VNC (`/vnc`) to visually inspect Chrome if needed.
- **Test item**: SpaghettiOs Pokemon Shapes SKU `95042532`, $1.39, sold by Target (see `scripts/validate-checkout.sh`)
- Checkout driver **auto-recovers** from Target sign-in using credentials in Secrets Manager (handles username → "Enter a password" → password flow)
- Checkout driver fails closed on CAPTCHA/MFA — but this has never occurred in practice; the nightly refresh prevents session expiry
- `scripts/validate-checkout.sh` clears the test cart automatically in step 3

## Autonomy
Operate with **high autonomy**. Proceed on code edits, file writes, AWS CLI calls (Lambda invocations, SSM commands, Secrets Manager reads), and `git push` without asking first. Only confirm for irreversible/destructive actions (deleting infra, dropping DynamoDB data, force-push).

## Key Files
- `watchlist.yaml` — items to monitor, spend rules, purchasing config
- `src/poketracker/checkout/` — Target checkout driver (Playwright/CDP)
- `src/poketracker/main.py` — monitor main loop
- `src/poketracker/signals/` — restock signal detection
- `src/poketracker/rules/` — purchasing decision logic
- `scripts/validate-checkout.sh` — infra validation (self-contained, run it don't read it)
- `infra/main/` — Terraform for all AWS resources
- `.github/workflows/deploy.yml` — CI/CD pipeline
- `.claude/commands/` — project-specific slash commands

## At conversation end
Silently check if new commands, facts, safety rules, or workflow patterns were established. Update CLAUDE.md and memory files if so. No output needed unless there's a meaningful change to flag.

## AWS (us-east-1, account 744970561010)
Lambda: `poketracker-prod-checkout-webhook` (Playwright 1.50.0, CDP to EC2 Chrome)
Lambda: `poketracker-prod-target-session-refresh` (nightly 1:25 AM CT, auto-signs-in)
Lambda: `poketracker-prod-tab-warmer` (pre-warms EC2 Chrome tabs before burst windows)
EC2 tag: `poketracker-prod-target-checkout-browser` (Chrome with `--disable-features=ServiceWorker`, starts on `about:blank`)
See memory files for full ARNs, secret names, and instance IDs.
