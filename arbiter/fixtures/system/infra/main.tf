resource "aws_opensearch_domain" "vectors" {
  domain_name = "platform-vectors"

  encrypt_at_rest {
    enabled = true
  }
}

resource "aws_security_group" "app" {
  name = "platform-app"

  ingress {
    from_port   = 8443
    to_port     = 8443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }
}
