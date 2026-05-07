locals {
  project     = "PokeTracker"
  environment = "prod"
  name_prefix = "poketracker-prod"

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

resource "aws_ecr_repository" "app" {
  name                 = "${local.name_prefix}-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
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
          aws_secretsmanager_secret.github_app.arn
        ]
      }
    ]
  })
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
        { name = "BESTBUY_API_KEY_SECRET_ARN", value = aws_secretsmanager_secret.bestbuy_api_key.arn },
        { name = "GITHUB_APP_SECRET_ARN", value = aws_secretsmanager_secret.github_app.arn }
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
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "events.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
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
