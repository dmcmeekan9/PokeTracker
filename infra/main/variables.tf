variable "aws_region" {
  description = "AWS region for PokeTracker resources."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "GitHub repository in owner/name form allowed to assume the deploy role."
  type        = string
}

variable "alert_sender_email" {
  description = "SES sender email identity."
  type        = string
  default     = "poketracker@proton.me"
}

variable "alert_recipient_email" {
  description = "Email address that receives PokeTracker alerts."
  type        = string
}

variable "schedule_expression" {
  description = "EventBridge schedule expression for the ECS task."
  type        = string
  default     = "rate(1 minute)"
}

variable "image_tag" {
  description = "Container image tag used by the ECS task definition."
  type        = string
  default     = "latest"
}

variable "email_footer_gif_url" {
  description = "Optional public HTTPS GIF URL rendered at the bottom of HTML alert emails."
  type        = string
  default     = "https://www.gifcen.com/wp-content/uploads/2023/03/-8.gif"
}

variable "checkout_webhook_url" {
  description = "Optional external HTTPS endpoint that accepts v2 purchase requests. Leave empty to use the managed Lambda webhook."
  type        = string
  default     = ""

  validation {
    condition     = var.checkout_webhook_url == "" || startswith(var.checkout_webhook_url, "https://")
    error_message = "checkout_webhook_url must be empty or an https:// URL."
  }
}

variable "managed_checkout_webhook_enabled" {
  description = "Whether to create and use the managed Lambda checkout webhook when checkout_webhook_url is empty."
  type        = bool
  default     = true
}

variable "target_place_order_enabled" {
  description = "Whether the Target checkout driver is allowed to click the final place-order control."
  type        = bool
  default     = false
}

variable "target_session_refresh_enabled" {
  description = "Whether to create the managed Lambda that refreshes the Target browser session inside AWS."
  type        = bool
  default     = true
}

variable "target_session_refresh_schedule_expression" {
  description = "EventBridge schedule for the managed Target session refresh Lambda. Leave empty to disable the schedule."
  type        = string
  default     = "cron(45 6,7 * * ? *)"
}

variable "target_session_verify_url" {
  description = "Optional Target product URL opened during the managed AWS session refresh to preflight the cart/session state."
  type        = string
  default     = ""
}

variable "target_checkout_browser_enabled" {
  description = "Whether to create a persistent EC2-hosted Chrome session for Target checkout and point the managed webhook at it over private CDP."
  type        = bool
  default     = true
}

variable "target_checkout_browser_instance_type" {
  description = "EC2 instance type for the persistent Target Chrome checkout browser."
  type        = string
  default     = "t3a.small"
}

variable "target_checkout_browser_volume_size" {
  description = "Root EBS volume size in GiB for the persistent Target Chrome checkout browser profile."
  type        = number
  default     = 20
}

variable "checkout_webhook_image_uri" {
  description = "Container image URI for the managed checkout webhook Lambda. When empty, Terraform creates only the ECR repository."
  type        = string
  default     = ""
}
