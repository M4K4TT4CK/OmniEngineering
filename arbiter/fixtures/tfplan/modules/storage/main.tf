variable "regions" {
  type = list(string)
}

resource "aws_s3_bucket" "data" {
  for_each = toset(var.regions)
  bucket   = "acme-data-${each.value}"
  acl      = each.value == "us" ? "public-read" : "private"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = { for k, v in aws_s3_bucket.data : k => v if k == "eu" }
  bucket   = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}
