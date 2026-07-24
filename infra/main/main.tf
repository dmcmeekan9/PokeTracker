locals {
  project                          = "PokeTracker"
  environment                      = "prod"
  name_prefix                      = "poketracker-prod"
  checkout_webhook_lambda_enabled  = var.managed_checkout_webhook_enabled && var.checkout_webhook_image_uri != ""
  target_checkout_browser_enabled  = local.checkout_webhook_lambda_enabled && var.target_checkout_browser_enabled
  target_session_refresh_enabled   = local.checkout_webhook_lambda_enabled && var.target_session_refresh_enabled
  target_session_refresh_scheduled = local.target_session_refresh_enabled && var.target_session_refresh_schedule_expression != ""
  target_tab_warmup_enabled        = local.target_checkout_browser_enabled && var.target_warmup_urls != ""
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

data "aws_ami" "ubuntu" {
  count       = local.target_checkout_browser_enabled ? 1 : 0
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
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

data "aws_secretsmanager_secret" "target_credentials" {
  name = "${local.name_prefix}-target-credentials"
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
    from_port = 443
    to_port   = 443
    protocol  = "tcp"
    security_groups = [
      aws_security_group.checkout_webhook_lambda[0].id,
      aws_security_group.task.id,
      aws_security_group.target_checkout_browser[0].id
    ]
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

resource "aws_vpc_endpoint" "ssm" {
  count               = local.target_checkout_browser_enabled ? 1 : 0
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ssm"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [aws_subnet.public[0].id]
  security_group_ids  = [aws_security_group.vpc_endpoint[0].id]
}

resource "aws_vpc_endpoint" "ec2messages" {
  count               = local.target_checkout_browser_enabled ? 1 : 0
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ec2messages"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [aws_subnet.public[0].id]
  security_group_ids  = [aws_security_group.vpc_endpoint[0].id]
}

resource "aws_vpc_endpoint" "ssmmessages" {
  count               = local.target_checkout_browser_enabled ? 1 : 0
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ssmmessages"
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

resource "aws_iam_role_policy_attachment" "checkout_webhook_vpc_access" {
  count      = local.checkout_webhook_lambda_enabled ? 1 : 0
  role       = aws_iam_role.checkout_webhook[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
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
          data.aws_secretsmanager_secret.target_session.arn,
          data.aws_secretsmanager_secret.target_credentials.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:SendCommand"
        ]
        Resource = concat(
          local.target_checkout_browser_enabled ? [aws_instance.target_checkout_browser[0].arn] : [],
          [
            "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript",
            "arn:aws:ssm:${var.aws_region}:*:document/AWS-RunShellScript"
          ]
        )
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetCommandInvocation"
        Resource = "*"
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
  ami                         = data.aws_ami.ubuntu[0].id
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
    apt-get install -y ca-certificates curl gnupg nginx openbox snapd xvfb x11vnc
    snap install amazon-ssm-agent --classic || true
    systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || systemctl enable --now amazon-ssm-agent
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
    Wants=network-online.target poketracker-cdp-proxy.service

    [Service]
    User=poketracker
    Environment=DISPLAY=:1
    ExecStartPre=/bin/sh -c 'rm -rf "/opt/poketracker/chrome-profile/Default/Service Worker" "/opt/poketracker/chrome-profile/Default/Last Session" "/opt/poketracker/chrome-profile/Default/Last Tabs"'
    ExecStart=/usr/bin/google-chrome-stable --remote-debugging-address=127.0.0.1 --remote-debugging-port=9223 --remote-allow-origins=* --user-data-dir=/opt/poketracker/chrome-profile --no-first-run --no-restore-last-session --disable-dev-shm-usage --disable-features=ServiceWorker --window-size=1365,900 about:blank
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    UNIT

    cat >/etc/nginx/conf.d/cdp-upgrade-map.conf <<'NGINX'
    map $http_upgrade $connection_upgrade {
      default upgrade;
      ""      close;
    }
    NGINX

    cat >/etc/nginx/sites-available/poketracker-cdp <<'NGINX'
    server {
      listen 9222;

      location / {
        proxy_pass http://127.0.0.1:9223;
        proxy_http_version 1.1;
        proxy_set_header Host 127.0.0.1:9223;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 300s;
      }
    }
    NGINX
    rm -f /etc/nginx/sites-enabled/default
    ln -sf /etc/nginx/sites-available/poketracker-cdp /etc/nginx/sites-enabled/poketracker-cdp

    cat >/etc/systemd/system/poketracker-cdp-proxy.service <<'UNIT'
    [Unit]
    Description=PokeTracker CDP reverse proxy for Lambda access
    After=network-online.target poketracker-chrome.service nginx.service
    Wants=network-online.target
    Requires=poketracker-chrome.service nginx.service
    PartOf=poketracker-chrome.service

    [Service]
    Type=oneshot
    RemainAfterExit=yes
    ExecStart=/bin/systemctl reload nginx
    ExecStop=/bin/true

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
    systemctl enable --now nginx poketracker-display poketracker-openbox poketracker-chrome poketracker-cdp-proxy poketracker-vnc
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
  memory_size   = var.checkout_webhook_memory_size

  environment {
    variables = {
      CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN = data.aws_secretsmanager_secret.checkout_webhook_token.arn
      CHECKOUT_PROFILE_SECRET_ARN       = data.aws_secretsmanager_secret.checkout_profile.arn
      TARGET_BROWSER_INSTANCE_ID        = local.target_checkout_browser_enabled ? aws_instance.target_checkout_browser[0].id : ""
      TARGET_CDP_URL                    = local.target_checkout_browser_enabled ? "http://${aws_instance.target_checkout_browser[0].private_ip}:9222" : ""
      TARGET_SESSION_SECRET_ARN         = data.aws_secretsmanager_secret.target_session.arn
      TARGET_CREDENTIALS_SECRET_ARN     = data.aws_secretsmanager_secret.target_credentials.arn
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
    aws_iam_role_policy_attachment.checkout_webhook_vpc_access,
    aws_vpc_endpoint.secretsmanager,
    aws_vpc_endpoint.ssm,
  ]
}

resource "aws_lambda_function" "target_session_refresh" {
  count         = local.target_session_refresh_enabled ? 1 : 0
  function_name = "${local.name_prefix}-target-session-refresh"
  role          = aws_iam_role.checkout_webhook[0].arn
  package_type  = "Image"
  image_uri     = var.checkout_webhook_image_uri
  timeout       = 180
  memory_size   = 1024

  image_config {
    command = ["poketracker.checkout_webhook.session_refresh.lambda_handler"]
  }

  environment {
    variables = {
      TARGET_SESSION_SECRET_ARN     = data.aws_secretsmanager_secret.target_session.arn
      TARGET_CREDENTIALS_SECRET_ARN = data.aws_secretsmanager_secret.target_credentials.arn
      TARGET_BROWSER_INSTANCE_ID    = local.target_checkout_browser_enabled ? aws_instance.target_checkout_browser[0].id : ""
      TARGET_CDP_URL                = local.target_checkout_browser_enabled ? "http://${aws_instance.target_checkout_browser[0].private_ip}:9222" : ""
      TARGET_SESSION_VERIFY_URL     = var.target_session_verify_url
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
    aws_iam_role_policy_attachment.checkout_webhook_vpc_access,
    aws_vpc_endpoint.secretsmanager,
    aws_vpc_endpoint.ssm,
  ]
}

resource "aws_lambda_function" "tab_warmer" {
  count         = local.target_tab_warmup_enabled ? 1 : 0
  function_name = "${local.name_prefix}-tab-warmer"
  role          = aws_iam_role.checkout_webhook[0].arn
  package_type  = "Image"
  image_uri     = var.checkout_webhook_image_uri
  timeout       = 120
  memory_size   = 1024

  image_config {
    command = ["poketracker.checkout_webhook.tab_warmup.lambda_handler"]
  }

  environment {
    variables = {
      TARGET_CDP_URL             = "http://${aws_instance.target_checkout_browser[0].private_ip}:9222"
      TARGET_BROWSER_INSTANCE_ID = aws_instance.target_checkout_browser[0].id
      TARGET_SESSION_SECRET_ARN  = data.aws_secretsmanager_secret.target_session.arn
      TARGET_WARMUP_URLS         = var.target_warmup_urls
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.public[0].id]
    security_group_ids = [aws_security_group.checkout_webhook_lambda[0].id]
  }

  depends_on = [
    aws_iam_role_policy.checkout_webhook,
    aws_iam_role_policy_attachment.checkout_webhook_basic,
    aws_iam_role_policy_attachment.checkout_webhook_vpc_access,
    aws_vpc_endpoint.secretsmanager,
    aws_vpc_endpoint.ssm,
  ]
}

resource "aws_cloudwatch_event_rule" "tab_warmer" {
  count               = local.target_tab_warmup_enabled ? 1 : 0
  name                = "${local.name_prefix}-tab-warmer"
  schedule_expression = "rate(5 minutes)"
  state               = "DISABLED"
}

resource "aws_cloudwatch_event_target" "tab_warmer" {
  count     = local.target_tab_warmup_enabled ? 1 : 0
  rule      = aws_cloudwatch_event_rule.tab_warmer[0].name
  target_id = "tab-warmer"
  arn       = aws_lambda_function.tab_warmer[0].arn
}

resource "aws_lambda_permission" "tab_warmer_events" {
  count         = local.target_tab_warmup_enabled ? 1 : 0
  statement_id  = "AllowEventBridgeTabWarmer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.tab_warmer[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.tab_warmer[0].arn
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
        { name = "TARGET_SESSION_SECRET_ARN", value = data.aws_secretsmanager_secret.target_session.arn },
        { name = "TARGET_STOCK_PROBE_ITEM_IDS", value = "target-ascended-heroes-etb,target-ascended-heroes-booster-bundle,target-ascended-heroes-poster-collection,target-temporal-forces-etb,target-temporal-forces-iron-leaves-etb,target-paldean-fates-etb,target-paldean-fates-bb,target-destined-rivals-booster-bundle,target-prismatic-evolutions-etb,target-prismatic-evolutions-booster-bundle" },
        { name = "TARGET_STOCK_PROBE_COOLDOWN_SECONDS", value = "300" }
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

resource "aws_cloudwatch_event_rule" "target_burst" {
  # Target polling is handled exclusively by this burst window.
  # All Target checks run here at 5-sec intervals for 130 min (1:55–4:05 AM CT).
  # When adding Walmart or Best Buy, create a separate retailer-specific rule below.
  # CDT (UTC-5, Mar–Nov): 6:55 UTC = 1:55 AM CT
  # CST (UTC-6, Nov–Mar): 6:55 UTC = 12:55 AM CT (1 hr early — still covers restock window)
  name                = "${local.name_prefix}-target-burst"
  schedule_expression = "cron(55 6 * * ? *)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "target_burst" {
  rule     = aws_cloudwatch_event_rule.target_burst.name
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
          { name = "POKETRACKER_BURST_DURATION_SECONDS", value = "7800" },
          { name = "POKETRACKER_BURST_INTERVAL_SECONDS", value = "5" }
        ]
      }
    ]
  })

  depends_on = [aws_iam_role_policy.eventbridge]
}

resource "aws_cloudwatch_metric_alarm" "target_checkout_browser_recovery" {
  count               = local.target_checkout_browser_enabled ? 1 : 0
  alarm_name          = "${local.name_prefix}-target-checkout-browser-recovery"
  alarm_description   = "Auto-recover the Target checkout Chrome instance on system status check failure."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed_System"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  alarm_actions       = ["arn:aws:automate:${var.aws_region}:ec2:recover"]

  dimensions = {
    InstanceId = aws_instance.target_checkout_browser[0].id
  }
}

resource "aws_iam_role" "ec2_scheduler" {
  count = local.target_checkout_browser_enabled ? 1 : 0
  name  = "${local.name_prefix}-ec2-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "scheduler.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ec2_scheduler" {
  count = local.target_checkout_browser_enabled ? 1 : 0
  name  = "${local.name_prefix}-ec2-scheduler"
  role  = aws_iam_role.ec2_scheduler[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ec2:StartInstances", "ec2:StopInstances"]
      Resource = aws_instance.target_checkout_browser[0].arn
    }]
  })
}

resource "aws_scheduler_schedule" "ec2_start" {
  count = local.target_checkout_browser_enabled ? 1 : 0
  name  = "${local.name_prefix}-ec2-start"

  flexible_time_window {
    mode = "OFF"
  }

  # 6:15 UTC = 1:15 AM CDT (UTC-5) / 12:15 AM CST (UTC-6)
  # 10 min before session refresh (6:25 UTC), 40 min before burst (6:55 UTC).
  schedule_expression = "cron(15 6 * * ? *)"

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:startInstances"
    role_arn = aws_iam_role.ec2_scheduler[0].arn
    input    = jsonencode({ InstanceIds = [aws_instance.target_checkout_browser[0].id] })
  }
}

resource "aws_scheduler_schedule" "ec2_stop" {
  count = local.target_checkout_browser_enabled ? 1 : 0
  name  = "${local.name_prefix}-ec2-stop"

  flexible_time_window {
    mode = "OFF"
  }

  # 9:20 UTC = 4:20 AM CDT (UTC-5) / 3:20 AM CST (UTC-6)
  # 15 min after burst ends (6:55 UTC + 130 min = 9:05 UTC).
  schedule_expression = "cron(20 9 * * ? *)"

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = aws_iam_role.ec2_scheduler[0].arn
    input    = jsonencode({ InstanceIds = [aws_instance.target_checkout_browser[0].id] })
  }
}
