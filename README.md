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

## V2 Purchasing

`global.purchasing_enabled` is set to `true` in `watchlist.yaml`. By default Terraform creates a managed Lambda checkout webhook and passes its Function URL to the ECS task. Set the GitHub Actions variable `CHECKOUT_WEBHOOK_URL` only when you want to use an external checkout service instead.

The webhook receives item, price, quantity, and weekly spend context. A successful 2xx response is recorded as `PURCHASED`; failures are recorded as `PURCHASE_FAILED` and alerted. The managed webhook is protected by the bearer token in the manually managed Secrets Manager secret named `poketracker-prod-checkout-webhook-token`, also exposed by the `checkout_webhook_token_secret_arn` Terraform output.

The default checkout profile lives in the manually managed Secrets Manager secret named `poketracker-prod-checkout-profile`, also exposed by the `checkout_profile_secret_arn` Terraform output. It supports shipping/contact details plus either a saved retailer payment reference or a payment token reference. It intentionally rejects raw card numbers, CVV/CVC, and expiration fields.

The managed webhook includes a Target browser driver. It uses a manually captured Target session, saved Target shipping/payment, and Playwright running in a Lambda container image. It fails closed if Target asks for sign-in, MFA, CAPTCHA, a payment security code, or if `TARGET_PLACE_ORDER_ENABLED` is not `true`.

Successful purchases are recorded in the state table so weekly spend caps include real purchase activity and the same item is not purchased again in the same configured week.

Target restock monitoring runs every minute all day. Additional EventBridge burst windows currently start at 2:00 AM and 3:00 AM Central Daylight Time; each burst checks repeatedly for 10 minutes with a 10-second interval.

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

To allow final purchase submission in deploy, set:

```powershell
gh variable set TARGET_PLACE_ORDER_ENABLED --body "true"
```

## AWS Setup Notes

1. Run `infra/bootstrap` once to create the Terraform state bucket and lock table.
2. Configure `infra/main/terraform.tfvars` from `infra/main/terraform.tfvars.example`.
3. Apply `infra/main` once from a trusted local/admin AWS session to create the GitHub OIDC role.
4. Add the `github_actions_role_arn` output as the GitHub Actions variable `AWS_ROLE_ARN`.
5. Verify SES identities sent to `poketrackerx@gmail.com` and your recipient email.
6. Put the Best Buy API key into the Secrets Manager secret created by Terraform.

## Branch Protection

Enable branch protection on `main` in GitHub:

- Require pull request before merging.
- Require the `validate` workflow check.
- Require branches to be up to date before merging.
