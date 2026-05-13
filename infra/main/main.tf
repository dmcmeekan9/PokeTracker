locals {
  project                          = "PokeTracker"
  environment                      = "prod"
  name_prefix                      = "poketracker-prod"
  checkout_webhook_lambda_enabled  = var.managed_checkout_webhook_enabled && var.checkout_webhook_image_uri != ""
  target_checkout_browser_enabled  = local.checkout_webhook_lambda_enabled && var.target_checkout_browser_enabled
  target_session_refresh_enabled   = local.checkout_webhook_lambda_enabled && var.target_session_refresh_enabled
  target_session_refresh_scheduled = local.target_session_refresh_enabled && var.target_session_refresh_schedule_expression != ""
  checkout_webhook_url = var.checkout_webhook_url != "" ? var.checkout_webhook_url : (
    local.checkout_webhook_lambda_enabled ? aws_lambda_function_url.checkout_webhook[0].function_url : ""
  )

  tags = {
    Project     = local.project
    Environment = local.environment
    ManagedBy   = "Terraform"
    Owner       = "personal"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
}

resource "aws_ecr_repository" "app" {
  name                 = "${local.name_prefix}-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "checkout_webhook" {
  name                 = "${local.name_prefix}-checkout-webhook"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  depends_on = [aws_iam_role_policy.github_actions]
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 30
}

resource "aws_dynamodb_table" "config" {
  name         = "${local.name_prefix}-config"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}

resource "aws_dynamodb_table" "audit" {
  name         = "${local.name_prefix}-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "state" {
  name         = "${local.name_prefix}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}

resource "aws_ses_email_identity" "sender" {
  email = var.alert_sender_email
}

resource "aws_ses_email_identity" "recipient" {
  email = var.alert_recipient_email
}

resource "aws_secretsmanager_secret" "bestbuy_api_key" {
  name        = "${local.name_prefix}-bestbuy-api-key"
  description = "Best Buy developer API key used by the PokeTracker ECS task."
}

resource "aws_secretsmanager_secret" "github_app" {
  name        = "${local.name_prefix}-github-app"
  description = "Future GitHub App credentials for bot-created pull requests."
}

data "aws_secretsmanager_secret" "checkout_webhook_token" {
  name = "${local.name_prefix}-checkout-webhook-token"
}

data "aws_secretsmanager_secret" "checkout_profile" {
  name = "${local.name_prefix}-checkout-profile"
}

data "aws_secretsmanager_secret" "target_session" {
  name = "${local.name_prefix}-target-session"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "task" {
  name        = "${local.name_prefix}-task"
  description = "Egress-only security group for PokeTracker Fargate task."
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "checkout_webhook_lambda" {
  count       = local.target_checkout_browser_enabled ? 1 : 0
  name        = "${local.name_prefix}-checkout-webhook-lambda"
  description = "Managed checkout Lambda egress to the EC2-hosted Target browser and VPC endpoints."
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "target_checkout_browser" {
  count       = local.target_checkout_browser_enabled ? 1 : 0
  name        = "${local.name_prefix}-target-checkout-browser"
  description = "Private CDP access for the persistent Target checkout Chrome instance."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Chrome DevTools Protocol from checkout Lambda"
    from_port       = 9222
    to_port         = 9222
    protocol        = "tcp"
    security_groups = [aws_security_group.checkout_webhook_lambda[0].id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "vpc_endpoint" {
  count       = local.target_checkout_browser_enabled ? 1 : 0
  name        = "${local.name_prefix}-vpc-endpoint"
  description = "Interface endpoint access from checkout Lambda."
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.checkout_webhook_lambda[0].id]
  }
}

resource "aws_vpc_endpoint" "secretsmanager" {
  count               = local.target_checkout_browser_enabled ? 1 : 0
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [aws_subnet.public[0].id]
  security_group_ids  = [aws_security_group.vpc_endpoint[0].id]
}

resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"
}

resource "aws_iam_role" "task_execution" {
  name = "${local.name_prefix}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name = "${local.name_prefix}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "task" {
  name = "${local.name_prefix}-task"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Scan",
          "dynamodb:Query",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.config.arn,
          aws_dynamodb_table.audit.arn,
          aws_dynamodb_table.state.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.bestbuy_api_key.arn,
          aws_secretsmanager_secret.github_app.arn,
          data.aws_secretsmanager_secret.checkout_webhook_token.arn,
          data.aws_secretsmanager_secret.checkout_profile.arn,
          data.aws_secretsmanager_secret.target_session.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role" "checkout_webhook" {
  count = local.checkout_webhook_lambda_enabled ? 1 : 0
  name  = "${local.name_prefix}-checkout-webhook"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "checkout_webhook_basic" {
  count      = local.checkout_webhook_lambda_enabled ? 1 : 0
  role       = aws_iam_role.checkout_webhook[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "checkout_webhook" {
  count = local.checkout_webhook_lambda_enabled ? 1 : 0
  name  = "${local.name_prefix}-checkout-webhook"
  role  = aws_iam_role.checkout_webhook[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue"
        ]
        Resource = [
          data.aws_secretsmanager_secret.checkout_webhook_token.arn,
          data.aws_secretsmanager_secret.checkout_profile.arn,
          data.aws_secretsmanager_secret.target_session.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role" "target_checkout_browser" {
  count = local.target_checkout_browser_enabled ? 1 : 0
  name  = "${local.name_prefix}-target-checkout-browser"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "target_checkout_browser_ssm" {
  count      = local.target_checkout_browser_enabled ? 1 : 0
  role       = aws_iam_role.target_checkout_browser[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "target_checkout_browser" {
  count = local.target_checkout_browser_enabled ? 1 : 0
  name  = "${local.name_prefix}-target-checkout-browser"
  role  = aws_iam_role.target_checkout_browser[0].name
}

resource "aws_instance" "target_checkout_browser" {
  count                       = local.target_checkout_browser_enabled ? 1 : 0
  ami                         = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type               = var.target_checkout_browser_instance_type
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.target_checkout_browser[0].id]
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.target_checkout_browser[0].name

  root_block_device {
    volume_size = var.target_checkout_browser_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  user_data_replace_on_change = true
  user_data                   = <<-EOF
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive

    apt-get update
    apt-get install -y ca-certificates curl gnupg openbox xvfb x11vnc
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
    chmod a+r /etc/apt/keyrings/google-chrome.gpg
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
    apt-get update
    apt-get install -y google-chrome-stable

    useradd --system --create-home --home-dir /opt/poketracker --shell /usr/sbin/nologin poketracker || true
    mkdir -p /opt/poketracker/chrome-profile
    chown -R poketracker:poketracker /opt/poketracker

    cat >/etc/systemd/system/poketracker-display.service <<'UNIT'
    [Unit]
    Description=PokeTracker checkout X display
    After=network-online.target
    Wants=network-online.target

    [Service]
    User=poketracker
    Environment=DISPLAY=:1
    ExecStart=/usr/bin/Xvfb :1 -screen 0 1365x900x24 -ac
    Restart=always
    RestartSec=2

    [Install]
    WantedBy=multi-user.target
    UNIT

    cat >/etc/systemd/system/poketracker-openbox.service <<'UNIT'
    [Unit]
    Description=PokeTracker checkout window manager
    After=poketracker-display.service
    Requires=poketracker-display.service

    [Service]
    User=poketracker
    Environment=DISPLAY=:1
    ExecStart=/usr/bin/openbox
    Restart=always
    RestartSec=2

    [Install]
    WantedBy=multi-user.target
    UNIT

    cat >/etc/systemd/system/poketracker-chrome.service <<'UNIT'
    [Unit]
    Description=PokeTracker persistent Target checkout Chrome
    After=poketracker-display.service network-online.target
    Requires=poketracker-display.service
    Wants=network-online.target

    [Service]
    User=poketracker
    Environment=DISPLAY=:1
    ExecStart=/usr/bin/google-chrome-stable --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=/opt/poketracker/chrome-profile --no-first-run --disable-dev-shm-usage --window-size=1365,900 https://www.target.com/account
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    UNIT

    cat >/etc/systemd/system/poketracker-vnc.service <<'UNIT'
    [Unit]
    Description=PokeTracker checkout VNC over SSM port forwarding
    After=poketracker-display.service
    Requires=poketracker-display.service

    [Service]
    User=poketracker
    Environment=DISPLAY=:1
    ExecStart=/usr/bin/x11vnc -display :1 -localhost -rfbport 5901 -forever -shared -nopw
    Restart=always
    RestartSec=2

    [Install]
    WantedBy=multi-user.target
    UNIT

    systemctl daemon-reload
    systemctl enable --now poketracker-display poketracker-openbox poketracker-chrome poketracker-vnc
  EOF

  tags = {
    Name = "${local.name_prefix}-target-checkout-browser"
  }
}

resource "aws_lambda_function" "checkout_webhook" {
  count         = local.checkout_webhook_lambda_enabled ? 1 : 0
  function_name = "${local.name_prefix}-checkout-webhook"
  role          = aws_iam_role.checkout_webhook[0].arn
  package_type  = "Image"
  image_uri     = var.checkout_webhook_image_uri
  timeout       = local.target_checkout_browser_enabled ? 300 : 120
  memory_size   = 1024

  environment {
    variables = {
      CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN = data.aws_secretsmanager_secret.checkout_webhook_token.arn
      CHECKOUT_PROFILE_SECRET_ARN       = data.aws_secretsmanager_secret.checkout_profile.arn
      TARGET_CDP_URL                    = local.target_checkout_browser_enabled ? "http://${aws_instance.target_checkout_browser[0].private_ip}:9222" : ""
      TARGET_SESSION_SECRET_ARN         = data.aws_secretsmanager_secret.target_session.arn
      TARGET_SESSION_VERIFY_URL         = var.target_session_verify_url
      TARGET_PLACE_ORDER_ENABLED        = tostring(var.target_place_order_enabled)
    }
  }

  dynamic "vpc_config" {
    for_each = local.target_checkout_browser_enabled ? [1] : []
    content {
      subnet_ids         = [aws_subnet.public[0].id]
      security_group_ids = [aws_security_group.checkout_webhook_lambda[0].id]
    }
  }

  depends_on = [
    aws_iam_role_policy.github_actions,
    aws_iam_role_policy_attachment.checkout_webhook_basic,
    aws_vpc_endpoint.secretsmanager,
  ]
}

resource "aws_lambda_function" "target_session_refresh" {
  count         = local.target_session_refresh_enabled ? 1 : 0
  function_name = "${local.name_prefix}-target-session-refresh"
  role          = aws_iam_role.checkout_webhook[0].arn
  package_type  = "Image"
  image_uri     = var.checkout_webhook_image_uri
  timeout       = 60
  memory_size   = 1024

  image_config {
    command = ["poketracker.checkout_webhook.session_refresh.lambda_handler"]
  }

  environment {
    variables = {
      TARGET_SESSION_SECRET_ARN = data.aws_secretsmanager_secret.target_session.arn
      TARGET_SESSION_VERIFY_URL = var.target_session_verify_url
    }
  }

  depends_on = [
    aws_iam_role_policy.github_actions,
    aws_iam_role_policy_attachment.checkout_webhook_basic,
  ]
}

resource "aws_lambda_function_url" "checkout_webhook" {
  count              = local.checkout_webhook_lambda_enabled ? 1 : 0
  function_name      = aws_lambda_function.checkout_webhook[0].function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "checkout_webhook_invoke_url" {
  count                  = local.checkout_webhook_lambda_enabled ? 1 : 0
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.checkout_webhook[0].function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "checkout_webhook_invoke_function" {
  count         = local.checkout_webhook_lambda_enabled ? 1 : 0
  statement_id  = "FunctionURLInvokeAllowPublicAccess"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.checkout_webhook[0].function_name
  principal     = "*"
}

resource "aws_cloudwatch_event_rule" "target_session_refresh" {
  count               = local.target_session_refresh_scheduled ? 1 : 0
  name                = "${local.name_prefix}-target-session-refresh"
  schedule_expression = var.target_session_refresh_schedule_expression
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "target_session_refresh" {
  count     = local.target_session_refresh_scheduled ? 1 : 0
  rule      = aws_cloudwatch_event_rule.target_session_refresh[0].name
  target_id = "target-session-refresh"
  arn       = aws_lambda_function.target_session_refresh[0].arn
}

resource "aws_lambda_permission" "target_session_refresh_events" {
  count         = local.target_session_refresh_scheduled ? 1 : 0
  statement_id  = "AllowEventBridgeTargetSessionRefresh"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.target_session_refresh[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.target_session_refresh[0].arn
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name_prefix
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "poketracker"
      image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
      essential = true
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "CONFIG_TABLE_NAME", value = aws_dynamodb_table.config.name },
        { name = "AUDIT_TABLE_NAME", value = aws_dynamodb_table.audit.name },
        { name = "STATE_TABLE_NAME", value = aws_dynamodb_table.state.name },
        { name = "ALERT_SENDER_EMAIL", value = var.alert_sender_email },
        { name = "ALERT_RECIPIENT_EMAIL", value = var.alert_recipient_email },
        { name = "EMAIL_FOOTER_GIF_URL", value = var.email_footer_gif_url },
        { name = "BESTBUY_API_KEY_SECRET_ARN", value = aws_secretsmanager_secret.bestbuy_api_key.arn },
        { name = "GITHUB_APP_SECRET_ARN", value = aws_secretsmanager_secret.github_app.arn },
        { name = "CHECKOUT_WEBHOOK_URL", value = local.checkout_webhook_url },
        { name = "CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", value = data.aws_secretsmanager_secret.checkout_webhook_token.arn },
        { name = "CHECKOUT_PROFILE_SECRET_ARN", value = data.aws_secretsmanager_secret.checkout_profile.arn },
        { name = "TARGET_SESSION_SECRET_ARN", value = data.aws_secretsmanager_secret.target_session.arn }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.app.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}

resource "aws_iam_role" "eventbridge" {
  name = "${local.name_prefix}-eventbridge"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "eventbridge" {
  name = "${local.name_prefix}-eventbridge"
  role = aws_iam_role.eventbridge.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ecs:RunTask"
        Resource = aws_ecs_task_definition.app.arn
      },
      {
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          aws_iam_role.task_execution.arn,
          aws_iam_role.task.arn
        ]
      }
    ]
  })
}

resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${local.name_prefix}-schedule"
  schedule_expression = var.schedule_expression
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "task" {
  rule     = aws_cloudwatch_event_rule.schedule.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.app.arn
    launch_type         = "FARGATE"

    network_configuration {
      assign_public_ip = true
      subnets          = aws_subnet.public[*].id
      security_groups  = [aws_security_group.task.id]
    }
  }
}

resource "aws_cloudwatch_event_rule" "target_burst" {
  for_each = {
    "2am" = "cron(0 7 * * ? *)"
    "3am" = "cron(0 8 * * ? *)"
  }

  name                = "${local.name_prefix}-target-burst-${each.key}"
  schedule_expression = each.value
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "target_burst" {
  for_each = aws_cloudwatch_event_rule.target_burst

  rule     = each.value.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.app.arn
    launch_type         = "FARGATE"

    network_configuration {
      assign_public_ip = true
      subnets          = aws_subnet.public[*].id
      security_groups  = [aws_security_group.task.id]
    }
  }

  input = jsonencode({
    containerOverrides = [
      {
        name = "poketracker"
        environment = [
          { name = "POKETRACKER_BURST_DURATION_SECONDS", value = "600" },
          { name = "POKETRACKER_BURST_INTERVAL_SECONDS", value = "10" }
        ]
      }
    ]
  })

  depends_on = [aws_iam_role_policy.eventbridge]
}
