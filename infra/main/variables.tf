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
  default     = "poketrackerx@gmail.com"
}

variable "alert_recipient_email" {
  description = "Email address that receives PokeTracker alerts."
  type        = string
}

variable "schedule_expression" {
  description = "EventBridge schedule expression for the ECS task."
  type        = string
  default     = "rate(5 minutes)"
}

variable "image_tag" {
  description = "Container image tag used by the ECS task definition."
  type        = string
  default     = "latest"
}

variable "email_footer_gif_url" {
  description = "Optional public HTTPS GIF URL rendered at the bottom of HTML alert emails."
  type        = string
  default     = ""
}
