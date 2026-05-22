Run `bash scripts/validate-checkout.sh` from the project root.

**Before running the script**, do the following health checks and auto-recovery:

## Step 1: Verify EC2 Chrome instance state
Look up the instance by tag `poketracker-prod-target-checkout-browser` (never hardcode the instance ID).
- If stopped/stopping: start it and wait for `running` state, then wait 30 seconds for services to initialize.

## Step 2: Verify Chrome services are active
Run via SSM:
```bash
aws ssm send-command --instance-ids <instance-id> --document-name "AWS-RunShellScript" \
  --parameters '{"commands":["systemctl is-active poketracker-chrome poketracker-cdp-proxy","ss -tlnp | grep 922 || echo NO_CDP_PORTS"]}' \
  --region us-east-1 --query "Command.CommandId" --output text
```
Wait for the command to complete, then check output.

If `poketracker-chrome` or `poketracker-cdp-proxy` is not `active`, or if `NO_CDP_PORTS` appears:
- Restart Chrome and proxy via SSM:
  ```bash
  systemctl restart poketracker-chrome && sleep 10 && systemctl is-active poketracker-chrome poketracker-cdp-proxy
  ```
- Wait 10 seconds and confirm both are `active` before proceeding.

## Step 3: Run the validation script
```bash
bash scripts/validate-checkout.sh
```
This takes ~90 seconds. The script is self-contained; do not read it.

## Step 4: On failure — auto-recover and retry once

**If the script fails with a browser/CDP error** (e.g. `Browser.new_context`, `ECONNREFUSED`, `Target page...closed`):
1. Restart Chrome via SSM: `systemctl restart poketracker-chrome && sleep 15`
2. Re-run `bash scripts/validate-checkout.sh`

**If the script fails with a sign-in/session error** (e.g. `not signed in`, `sign-in`, session expired):
1. Invoke session refresh Lambda:
   ```bash
   aws lambda invoke --function-name poketracker-prod-target-session-refresh \
     --cli-binary-format raw-in-base64-out --payload '{}' --region us-east-1 /tmp/r.json && cat /tmp/r.json
   ```
2. Invoke tab warmer Lambda:
   ```bash
   aws lambda invoke --function-name poketracker-prod-tab-warmer \
     --cli-binary-format raw-in-base64-out --payload '{}' --region us-east-1 /tmp/w.json && cat /tmp/w.json
   ```
3. Re-run `bash scripts/validate-checkout.sh`

Only retry once. If it fails again, report the error without further retries.

## Report
- **Pass**: show the timing line from the script output
- **Fail** (after recovery attempt): show the exact error and which component to check (EC2, Lambda, or Target session)

Do not explain what the script does.
