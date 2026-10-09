# TLS in transit, one broken and one correct for each new rule.
#
# The correct half is the half that matters. A rule that cannot recognise a
# properly configured resource fires on everything, and every recall number
# still reads perfect — which is exactly how a volume encrypted with a
# customer-managed key came to be reported as unencrypted.

# ---- databases that accept unencrypted connections -----------------------

resource "azurerm_postgresql_server" "bad" {
  name                = "pg-bad"
  sku_name            = "GP_Gen5_2"
  version             = "11"
  # no ssl_enforcement_enabled: a client that does not ask for TLS gets a
  # plaintext session and no error
}

resource "azurerm_postgresql_server" "good" {
  name                     = "pg-good"
  sku_name                 = "GP_Gen5_2"
  version                  = "11"
  ssl_enforcement_enabled  = true
  ssl_minimal_tls_version_enforced = "TLS1_2"
}

resource "azurerm_mysql_server" "bad" {
  name     = "mysql-bad"
  sku_name = "GP_Gen5_2"
}

resource "azurerm_mysql_server" "good" {
  name                    = "mysql-good"
  sku_name                = "GP_Gen5_2"
  ssl_enforcement_enabled = true
}

resource "google_sql_database_instance" "tls_bad" {
  name             = "sql-bad"
  database_version = "POSTGRES_15"
  settings {
    tier = "db-f1-micro"
  }
}

resource "google_sql_database_instance" "tls_good" {
  name                = "sql-good"
  database_version    = "POSTGRES_15"
  encryption_key_name = google_kms_crypto_key.main.id
  deletion_protection = true
  settings {
    tier = "db-f1-micro"
    ip_configuration {
      require_ssl = true
    }
  }
}

# ---- TLS version policy ---------------------------------------------------

resource "azurerm_storage_account" "tls_bad" {
  name                = "stbad"
  min_tls_version     = "TLS1_0"
  customer_managed_key = azurerm_key_vault_key.main.id
}

resource "azurerm_storage_account" "tls_good" {
  name                 = "stgood"
  min_tls_version      = "TLS1_2"
  customer_managed_key = azurerm_key_vault_key.main.id
}

resource "aws_elasticsearch_domain" "bad" {
  domain_name = "search-bad"
  encrypt_at_rest {
    enabled = true
  }
  domain_endpoint_options {
    tls_security_policy = "Policy-Min-TLS-1-0-2019-07"
  }
}

resource "aws_elasticsearch_domain" "good" {
  domain_name = "search-good"
  encrypt_at_rest {
    enabled = true
  }
  domain_endpoint_options {
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }
}

# ---- HTTP that never becomes HTTPS ---------------------------------------

resource "azurerm_app_service" "bad" {
  name                = "app-bad"
  app_service_plan_id = azurerm_app_service_plan.main.id
}

resource "azurerm_app_service" "good" {
  name                = "app-good"
  app_service_plan_id = azurerm_app_service_plan.main.id
  https_only          = true
}
