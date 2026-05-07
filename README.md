# PokeTracker

PokeTracker is an AWS-hosted, Terraform-managed restock monitor for manually selected Pokemon ETBs and Booster Bundles. Version 1 performs MSRP validation, seller classification, email alerts, audit logging, and dry-run purchase decisions only.

It does not add items to cart, submit payment, bypass retailer protections, or perform unauthorized checkout automation.

## High-Level Flow

```text
watchlist.yaml
  -> GitHub Actions validates PRs
  -> main deploy builds/pushes container, applies Terraform, syncs DynamoDB
  -> EventBridge runs ECS Fargate every 5 minutes
  -> app checks configured signals, applies rules, sends SES email, writes audit events
```

## Local Development

```powershell
uv sync --extra dev
uv run pytest
uv run poketracker-validate-watchlist --file watchlist.yaml --skip-url-check
```

## Required GitHub Variables

- `AWS_ROLE_ARN`
- `AWS_REGION` default: `us-east-1`
- `ALERT_RECIPIENT_EMAIL`
- `TERRAFORM_STATE_BUCKET`
- `TERRAFORM_LOCK_TABLE`

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
