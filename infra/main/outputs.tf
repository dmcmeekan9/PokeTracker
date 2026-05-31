output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "checkout_webhook_ecr_repository_url" {
  value = aws_ecr_repository.checkout_webhook.repository_url
}

output "pokecenter_notifier_ecr_repository_url" {
  value = aws_ecr_repository.pokecenter_notifier.repository_url
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "config_table_name" {
  value = aws_dynamodb_table.config.name
}

output "audit_table_name" {
  value = aws_dynamodb_table.audit.name
}

output "state_table_name" {
  value = aws_dynamodb_table.state.name
}

output "bestbuy_api_key_secret_arn" {
  value = aws_secretsmanager_secret.bestbuy_api_key.arn
}

output "github_app_secret_arn" {
  value = aws_secretsmanager_secret.github_app.arn
}

output "checkout_webhook_token_secret_arn" {
  value = data.aws_secretsmanager_secret.checkout_webhook_token.arn
}

output "checkout_profile_secret_arn" {
  value = data.aws_secretsmanager_secret.checkout_profile.arn
}

output "target_session_secret_arn" {
  value = data.aws_secretsmanager_secret.target_session.arn
}

output "target_credentials_secret_arn" {
  value = data.aws_secretsmanager_secret.target_credentials.arn
}

output "managed_checkout_webhook_url" {
  value = try(aws_lambda_function_url.checkout_webhook[0].function_url, "")
}

output "target_session_refresh_function_name" {
  value = try(aws_lambda_function.target_session_refresh[0].function_name, "")
}

output "target_checkout_browser_instance_id" {
  value = try(aws_instance.target_checkout_browser[0].id, "")
}

output "target_checkout_browser_private_ip" {
  value = try(aws_instance.target_checkout_browser[0].private_ip, "")
}

output "target_checkout_browser_cdp_url" {
  value = try("http://${aws_instance.target_checkout_browser[0].private_ip}:9222", "")
}

output "effective_checkout_webhook_url" {
  value = local.checkout_webhook_url
}
