# PokeTracker

PokeTracker is an AWS-hosted, Terraform-managed restock monitor for manually selected Pokemon ETBs and Booster Bundles. Version 2 performs MSRP validation, seller classification, email alerts, audit logging, and optional purchase handoff through a configured checkout webhook.

## High-Level Flow

```text
watchlist.yaml
  -> GitHub Actions validates PRs
  -> main deploy builds/pushes container, applies Terraform, syncs DynamoDB
  -> EventBridge runs ECS Fargate every minute, with 2 AM/3 AM CT burst windows
  -> app checks configured signals, applies rules, optionally submits purchase requests, sends SES email, writes audit/state events
```

## Local Development

```powershell
uv sync --extra dev
uv run pytest
uv run poketracker-validate-watchlist --file watchlist.yaml --skip-url-check --managed-checkout-webhook
```

## Required GitHub Variables

- `AWS_ROLE_ARN`
- `AWS_REGION` default: `us-east-1`
- `ALERT_RECIPIENT_EMAIL`
- `TERRAFORM_STATE_BUCKET`
- `TERRAFORM_LOCK_TABLE`

Optional:

- `CHECKOUT_WEBHOOK_URL` for an external webhook instead of the managed Lambda webhook
- `TARGET_PLACE_ORDER_ENABLED` set to `true` only when the Target driver is allowed to click the final place-order button
- `TARGET_SESSION_REFRESH_ENABLED` default: `false`
- `TARGET_SESSION_REFRESH_SCHEDULE_EXPRESSION` default: `cron(45 6,7 * * ? *)`
- `TARGET_SESSION_VERIFY_URL` optional Target URL opened by the managed AWS session refresher
- `TARGET_CHECKOUT_BROWSER_ENABLED` default: `true`, creates an EC2-hosted persistent Chrome session for Target checkout
- `TARGET_CHECKOUT_BROWSER_INSTANCE_TYPE` default: `t3a.small`
- `TARGET_CHECKOUT_BROWSER_VOLUME_SIZE` default: `20`

## V2 Purchasing

`global.purchasing_enabled` is set to `true` in `watchlist.yaml`. By default Terraform creates a managed Lambda checkout webhook and passes its Function URL to the ECS task. Set the GitHub Actions variable `CHECKOUT_WEBHOOK_URL` only when you want to use an external checkout service instead.

The webhook receives item, price, quantity, and weekly spend context. A successful 2xx response is recorded as `PURCHASED`; failures are recorded as `PURCHASE_FAILED` and alerted. The managed webhook is protected by the bearer token in the manually managed Secrets Manager secret named `poketracker-prod-checkout-webhook-token`, also exposed by the `checkout_webhook_token_secret_arn` Terraform output.

The default checkout profile lives in the manually managed Secrets Manager secret named `poketracker-prod-checkout-profile`, also exposed by the `checkout_profile_secret_arn` Terraform output. It supports shipping/contact details plus either a saved retailer payment reference or a payment token reference. It intentionally rejects raw card numbers, CVV/CVC, and expiration fields.

The managed webhook includes a Target browser driver. It uses a manually captured Target session, saved Target shipping/payment, and Playwright running in a Lambda container image. Successful AWS checkout runs write the refreshed Target cookies back to Secrets Manager automatically. The driver still fails closed if Target asks for sign-in, MFA, CAPTCHA, a payment security code, or if `TARGET_PLACE_ORDER_ENABLED` is not `true`.

For a no-purchase readiness proof, send the checkout webhook payload with `"verify_only": true` or run the local verifier below. In verify-only mode the driver must reach and see the final Target place-order control, then returns `ready_to_place_order` without clicking it. Normal monitor-triggered purchase requests do not set this flag, so a disabled final order click still fails closed and is not recorded as purchased.

The MVP purchasing path is the EC2-hosted Chrome session below. The older managed `target-session-refresh` Lambda is now optional and disabled by default. If enabled, it opens a headless Target browser in AWS, reloads the stored session, optionally opens `TARGET_SESSION_VERIFY_URL`, and writes the refreshed cookies back to Secrets Manager. The default schedule runs at `cron(45 6,7 * * ? *)`, which is 1:45 AM and 2:45 AM in Central Daylight Time.

The deploy workflow can also be run manually from GitHub Actions with the `refresh_target_session_now` input set to `true`. That path deploys the latest code, then invokes the AWS refresh Lambda immediately if the optional refresher is enabled.

When `TARGET_CHECKOUT_BROWSER_ENABLED` is true, Terraform also creates a private EC2-hosted Chrome session and points the managed checkout webhook at it with `TARGET_CDP_URL`. The webhook still receives the synchronous purchase request, validates the bearer token/profile/price, then drives the persistent Chrome profile over the private VPC network. Chrome's remote debugging port is not public; it only accepts traffic from the checkout Lambda security group. Use SSM port forwarding to reach the VNC session when you need to sign in or clear a Target prompt.

The default hosted browser favors checkout speed over the absolute cheapest footprint: `c7i.large`, 20 GiB gp3, one public IPv4 address for outbound internet/SSM, and a single-AZ Secrets Manager VPC endpoint for the checkout Lambda. You can lower `TARGET_CHECKOUT_BROWSER_INSTANCE_TYPE` to `t3a.small` for cheaper always-on validation, but checkout will be more CPU constrained.

Successful purchases are recorded in the state table so weekly spend caps include real purchase activity and the same item is not purchased again in the same configured week.

Target restock monitoring runs every minute all day. Additional EventBridge burst windows currently start at 1:55 AM and 2:55 AM Central Daylight Time; each burst checks repeatedly for 10 minutes with a 10-second interval so checks are already active before common top-of-hour drops.

Expected webhook response fields are optional JSON:

```json
{
  "status": "ordered",
  "order_id": "ABC123",
  "message": "confirmed",
  "quantity": 1
}
```

Upload the profile from a local, ignored file:

```powershell
aws secretsmanager create-secret --name poketracker-prod-checkout-profile --description "Default v2 checkout profile for PokeTracker." --secret-string '{"configured":false}' --region us-east-1
Copy-Item checkout-profile.example.json checkout-profile.json
# Edit checkout-profile.json with real shipping/contact and a saved payment reference.
uv run poketracker-put-checkout-profile --file checkout-profile.json --secret-id poketracker-prod-checkout-profile
```

Capture and upload the Target session from a trusted local machine:

```powershell
python -m pip install playwright
python -m playwright install chromium
$env:PYTHONPATH = "src"
python -m poketracker.checkout.target_session --secret-id poketracker-prod-target-session
```

When the browser opens, sign in to Target, make sure the account has the correct default shipping address and saved payment method, then return to the terminal and press Enter. The local `target-session.json` file is ignored by git.

Before a known 2 AM Target drop window, refresh the Target session from a real browser and clear any Target challenge before uploading:

```powershell
$env:PYTHONPATH = "src"
python -m poketracker.checkout.target_session `
  --secret-id poketracker-prod-target-session `
  --browser-channel chrome `
  --verify-url "https://www.target.com/p/pok-233-mon-trading-card-game-mega-evolutions-phantasmal-flames-9-pocket-portfolio/-/A-95045259#lnk=sametab"
```

Use the browser window to clear CAPTCHA/sign-in/payment prompts and confirm the page/cart state is usable before pressing Enter in the terminal. The checkout driver intentionally fails closed if Target presents CAPTCHA, MFA, sign-in, payment verification, or another intervention to the unattended Lambda session.

Once that session is uploaded, the hosted Chrome profile is the preferred always-ready session. If Target starts challenging the unattended browser, connect through VNC, clear the prompt in the hosted Chrome window, and re-run a verify-only checkout proof.

Verify a no-purchase checkout locally with the captured session:

```powershell
$env:PYTHONPATH = "src"
uv run poketracker-verify-target-checkout --item-id target-ascended-heroes-etb
```

If the watchlist item is not currently buyable, use an in-stock Target product or category URL for the checkout proof:

```powershell
$env:PYTHONPATH = "src"
uv run poketracker-verify-target-checkout `
  --url "https://www.target.com/s/toothpaste" `
  --item-name "Target checkout verification item"
```

To prove the same flow through an already-open signed-in Chrome session:

```powershell
.\scripts\start_target_debug_chrome.ps1
$env:PYTHONPATH = "src"
uv run poketracker-verify-target-checkout `
  --cdp-url "http://127.0.0.1:9222" `
  --url "https://www.target.com/p/-/A-92285103" `
  --item-name "Target checkout verification item"
```

The expected successful output is a JSON object with `"status":"ready_to_place_order"`. Any sign-in, CAPTCHA, MFA, payment, or shipping mismatch response means the Target session/profile needs manual attention before unattended purchasing can be trusted.

For the EC2-hosted Chrome session, deploy first, then open a private VNC tunnel through SSM:

```powershell
cd infra/main
$instanceId = terraform output -raw target_checkout_browser_instance_id
aws ssm start-session `
  --target $instanceId `
  --document-name AWS-StartPortForwardingSession `
  --parameters portNumber=5901,localPortNumber=5901
```

Connect a local VNC client to `127.0.0.1:5901`, sign in to Target in the hosted Chrome window, confirm saved payment/shipping, then run a verify-only webhook test before enabling final ordering.

For a local-browser overnight burst, keep the machine awake and use an already-open debug Chrome profile. This is now the fallback path only when the EC2-hosted Chrome session is still being challenged by Target:

```powershell
.\scripts\start_target_debug_chrome.ps1
$env:PYTHONPATH = "src"
uv run poketracker-local-target-buyer `
  --refresh-session-first `
  --target-session-secret-id poketracker-prod-target-session `
  --wait-until 01:55 `
  --duration-seconds 4200 `
  --interval-seconds 10 `
  --place-order
```

That command waits until 1:55 AM in the watchlist timezone, asks you to clear any Target challenge in the attached browser, uploads the refreshed Target session back to AWS, and then monitors through roughly 3:05 AM. The computer must stay on, awake, and online for the full run.

To allow final purchase submission in deploy, set:

```powershell
gh variable set TARGET_PLACE_ORDER_ENABLED --body "true"
```

## AWS Setup Notes

1. Run `infra/bootstrap` once to create the Terraform state bucket and lock table.
2. Configure `infra/main/terraform.tfvars` from `infra/main/terraform.tfvars.example`.
3. Apply `infra/main` once from a trusted local/admin AWS session to create the GitHub OIDC role.
4. Add the `github_actions_role_arn` output as the GitHub Actions variable `AWS_ROLE_ARN`.
5. Verify SES identities sent to `poketracker@proton.me` and your recipient email.
6. Put the Best Buy API key into the Secrets Manager secret created by Terraform.

## Branch Protection

Enable branch protection on `main` in GitHub:

- Require pull request before merging.
- Require the `validate` workflow check.
- Require branches to be up to date before merging.
