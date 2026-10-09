variable "encrypt_volumes" {
  type    = bool
  default = false
}

module "storage" {
  source  = "./modules/storage"
  regions = ["eu", "us"]
}

resource "aws_db_instance" "primary" {
  identifier          = "acme-primary"
  engine              = "postgres"
  storage_encrypted   = false
  publicly_accessible = true
}

resource "aws_ebs_volume" "scratch" {
  size      = 100
  encrypted = var.encrypt_volumes
}
