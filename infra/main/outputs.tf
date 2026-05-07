output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
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
