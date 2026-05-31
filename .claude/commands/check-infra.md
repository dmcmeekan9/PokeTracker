Validate all active monitoring infrastructure: Target checkout and PokémonCenter notifier.

---

# Part 1 — Target checkout

## Step 1: Verify EC2 Chrome instance state
Look up the instance by tag `poketracker-prod-target-checkout-browser` (never hardcode the instance ID).
- If stopped/stopping: start it and wait for `running` state, then wait for SSM to come online. After it's up, **always run the session refresh + tab warmer Lambdas before proceeding** (Chrome was cold and may have stale or missing session state):
  ```bash
  aws lambda invoke --function-name poketracker-prod-target-session-refresh \
    --cli-binary-format raw-in-base64-out --payload '{}' --region us-east-1 /tmp/r.json && cat /tmp/r.json
  aws lambda invoke --function-name poketracker-prod-tab-warmer \
    --cli-binary-format raw-in-base64-out --payload '{}' --region us-east-1 /tmp/w.json && cat /tmp/w.json
  ```

## Step 2: Verify Chrome services are active
Run via SSM (single-line pass/fail output):
```bash
aws ssm send-command --instance-ids <instance-id> --document-name "AWS-RunShellScript" \
  --parameters '{"commands":["systemctl is-active poketracker-chrome poketracker-cdp-proxy && ss -tlnp | grep -q 922 && echo CHROME_OK || echo CHROME_FAIL"]}' \
  --region us-east-1 --query "Command.CommandId" --output text
```
Wait for the command to complete and check for `CHROME_OK`.

If output is `CHROME_FAIL`:
- Restart Chrome and proxy via SSM: `systemctl restart poketracker-chrome && sleep 10 && systemctl is-active poketracker-chrome poketracker-cdp-proxy`
- Confirm both are `active` before proceeding.

## Step 3: Run the Target validation script
```bash
bash scripts/validate-checkout.sh
```
This takes ~90 seconds. The script is self-contained; do not read it.

## Step 4: On Target failure — auto-recover and retry once

**IMPORTANT: Never diagnose failures by running CDP WebSocket inspection scripts (Python websocket scripts via SSM that connect to Chrome's debug port). These destabilize the Chrome CDP session and will cause subsequent Lambda invocations to fail with `Browser.new_context` / `Target page...closed` errors.**

For any failure (`target_place_order_not_found`, `Browser.new_context`, CDP error, sign-in error):
1. Restart Chrome via SSM: `systemctl restart poketracker-chrome && sleep 15`
2. Session refresh: `aws lambda invoke --function-name poketracker-prod-target-session-refresh --cli-binary-format raw-in-base64-out --payload '{}' --region us-east-1 /tmp/r.json && cat /tmp/r.json`
3. Tab warm: `aws lambda invoke --function-name poketracker-prod-tab-warmer --cli-binary-format raw-in-base64-out --payload '{}' --region us-east-1 /tmp/w.json && cat /tmp/w.json`
4. Re-run `bash scripts/validate-checkout.sh`

Only retry once. If it fails again, report the error without further retries.

---

# Part 2 — PokémonCenter notifier

Run these checks after Target passes. The PokéCenter Lambda is stateless (no browser, no session), so checks are simpler.

## Step 5: Verify Lambda and EventBridge rule are active
```bash
aws lambda get-function --function-name poketracker-prod-pokecenter-notifier \
  --region us-east-1 --query "Configuration.{State:State,LastStatus:LastUpdateStatus}" --output json

aws events describe-rule --name poketracker-prod-pokecenter-notifier \
  --region us-east-1 --query "{State:State,Schedule:ScheduleExpression}" --output json
```
Expected: `State: Active` for the Lambda, `State: ENABLED` for the EventBridge rule.

## Step 6: Invoke the Lambda to confirm end-to-end
```bash
aws lambda invoke --function-name poketracker-prod-pokecenter-notifier \
  --cli-binary-format raw-in-base64-out --payload '{}' \
  --region us-east-1 /tmp/pc.json && cat /tmp/pc.json
```
Expected response body: `"checked 3 item(s), notified: [...]"` (notified list may be empty — that's fine).

**If the body contains an error or fewer than 3 items checked:**
- `no pokemoncenter items enabled` → watchlist not synced to DynamoDB; run `uv run poketracker-sync-watchlist --file watchlist.yaml`
- HTTP error / `request failed` in response → Cloudflare may be blocking the Lambda IP; check CloudWatch logs at `/aws/lambda/poketracker-prod-pokecenter-notifier` for details
- `ResourceNotFoundException` or DynamoDB error → Lambda env vars or table names are wrong; check the Lambda configuration in AWS console

---

# Report
- **Pass**: confirm both `validate-checkout.sh` timing line and PokéCenter Lambda response
- **Fail**: show the exact error and which component (EC2/Target Lambda/Chrome session vs. PokéCenter Lambda/EventBridge/DynamoDB)
