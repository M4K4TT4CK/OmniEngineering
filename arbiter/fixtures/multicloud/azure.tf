# Azure resources, one broken and one correct for each rule that now reaches
# this provider. The correct half matters more than the broken half: a kind
# mapping without the provider's own property names produces a rule that fires
# on everything, and every number still looks perfect.

resource "azurerm_mssql_database" "bad" {
  name      = "app-db"
  server_id = azurerm_mssql_server.main.id
  # no transparent_data_encryption_enabled, no customer_managed_key
  public_network_access_enabled = true
}

resource "azurerm_mssql_database" "good" {
  name                                = "app-db-ok"
  server_id                           = azurerm_mssql_server.main.id
  transparent_data_encryption_enabled = true
  public_network_access_enabled       = false
  deletion_protection_enabled         = true
}

resource "azurerm_managed_disk" "bad" {
  name                 = "data"
  storage_account_type = "Premium_LRS"
  create_option        = "Empty"
  disk_size_gb         = 128
}

resource "azurerm_managed_disk" "good" {
  name                   = "data-ok"
  storage_account_type   = "Premium_LRS"
  create_option          = "Empty"
  disk_size_gb           = 128
  disk_encryption_set_id = azurerm_disk_encryption_set.main.id
}

resource "azurerm_servicebus_queue" "bad" {
  name = "events"
}

resource "azurerm_servicebus_queue" "good" {
  name                 = "events-ok"
  customer_managed_key = azurerm_key_vault_key.main.id
}
