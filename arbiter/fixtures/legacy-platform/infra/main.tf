provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "acme-build-artifacts"
  acl    = "public-read"
}

resource "aws_s3_bucket" "audit_archive" {
  bucket = "acme-audit-archive"
  server_side_encryption_configuration {
    rule {
      apply_server_side_encryption_by_default {
        sse_algorithm = "aws:kms"
      }
    }
  }
  logging {
    target_bucket = "acme-access-logs"
  }
}

resource "aws_db_instance" "primary" {
  identifier          = "acme-primary"
  engine              = "postgres"
  instance_class      = "db.t3.medium"
  publicly_accessible = true
  skip_final_snapshot = true
}

resource "aws_security_group" "web" {
  name = "acme-web"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name = "/acme/app"
}
