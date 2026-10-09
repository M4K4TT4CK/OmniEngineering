# Google Cloud. GCP encrypts most storage by default, so the question these
# rules actually answer is key custody: is there a customer-managed key, or is
# the data under a provider-held key you cannot rotate.

resource "google_sql_database_instance" "bad" {
  name             = "app-db"
  database_version = "POSTGRES_15"
  settings {
    tier = "db-f1-micro"
  }
}

resource "google_sql_database_instance" "good" {
  name                = "app-db-ok"
  database_version    = "POSTGRES_15"
  encryption_key_name = google_kms_crypto_key.main.id
  deletion_protection = true
  settings {
    tier = "db-f1-micro"
    # Added when the TLS rules landed: "good" has to mean good for every rule,
    # not just the ones that existed when the fixture was written. A fixture
    # whose correct half is only correct about some rules quietly stops being
    # able to catch the next false positive.
    ip_configuration {
      require_ssl = true
    }
  }
}

resource "google_compute_disk" "bad" {
  name = "data"
  size = 100
}

resource "google_compute_disk" "good" {
  name = "data-ok"
  size = 100
  disk_encryption_key {
    kms_key_self_link = google_kms_crypto_key.main.id
  }
}

resource "google_pubsub_topic" "bad" {
  name = "events"
}

resource "google_pubsub_topic" "good" {
  name         = "events-ok"
  kms_key_name = google_kms_crypto_key.main.id
}
