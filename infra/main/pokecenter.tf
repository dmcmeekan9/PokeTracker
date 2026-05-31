variable "pokecenter_notifier_image_uri" {
  description = "ECR image URI for the PokémonCenter notifier Lambda. Leave empty to skip Lambda deployment."
  type        = string
  default     = ""
}

variable "pokecenter_notifier_schedule_expression" {
  description = "EventBridge schedule for PokémonCenter stock checks."
  type        = string
  default     = "rate(30 minutes)"
}

locals {
  pokecenter_notifier_enabled = var.pokecenter_notifier_image_uri != ""
}

resource "aws_ecr_repository" "pokecenter_notifier" {
  name                 = "${local.name_prefix}-pokecenter-notifier"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = false
  }
}

resource "aws_iam_role" "pokecenter_notifier" {
  count = local.pokecenter_notifier_enabled ? 1 : 0
  name  = "${local.name_prefix}-pokecenter-notifier"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "pokecenter_notifier_basic" {
  count      = local.pokecenter_notifier_enabled ? 1 : 0
  role       = aws_iam_role.pokecenter_notifier[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "pokecenter_notifier" {
  count = local.pokecenter_notifier_enabled ? 1 : 0
  name  = "${local.name_prefix}-pokecenter-notifier"
  role  = aws_iam_role.pokecenter_notifier[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.config.arn,
          aws_dynamodb_table.audit.arn,
          aws_dynamodb_table.state.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = "*"
      },
    ]
  })
}

resource "aws_lambda_function" "pokecenter_notifier" {
  count         = local.pokecenter_notifier_enabled ? 1 : 0
  function_name = "${local.name_prefix}-pokecenter-notifier"
  role          = aws_iam_role.pokecenter_notifier[0].arn
  package_type  = "Image"
  image_uri     = var.pokecenter_notifier_image_uri
  timeout       = 120
  memory_size   = 256

  environment {
    variables = {
      CONFIG_TABLE_NAME      = aws_dynamodb_table.config.name
      AUDIT_TABLE_NAME       = aws_dynamodb_table.audit.name
      STATE_TABLE_NAME       = aws_dynamodb_table.state.name
      ALERT_SENDER_EMAIL     = var.alert_sender_email
      ALERT_RECIPIENT_EMAIL  = var.alert_recipient_email
      EMAIL_FOOTER_GIF_URL   = var.email_footer_gif_url
    }
  }

  depends_on = [aws_iam_role_policy_attachment.pokecenter_notifier_basic]
}

resource "aws_cloudwatch_event_rule" "pokecenter_notifier" {
  count               = local.pokecenter_notifier_enabled ? 1 : 0
  name                = "${local.name_prefix}-pokecenter-notifier"
  schedule_expression = var.pokecenter_notifier_schedule_expression
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "pokecenter_notifier" {
  count     = local.pokecenter_notifier_enabled ? 1 : 0
  rule      = aws_cloudwatch_event_rule.pokecenter_notifier[0].name
  target_id = "pokecenter-notifier"
  arn       = aws_lambda_function.pokecenter_notifier[0].arn
}

resource "aws_lambda_permission" "pokecenter_notifier_events" {
  count         = local.pokecenter_notifier_enabled ? 1 : 0
  statement_id  = "AllowEventBridgePokecenterNotifier"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pokecenter_notifier[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pokecenter_notifier[0].arn
}
