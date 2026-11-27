"""Constants for the ЖКХ integration."""

from datetime import timedelta

DOMAIN = "zhkh"
CONF_PROVIDER = "provider"
CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"

PROVIDER_TCE = "tce_crimea"
PROVIDER_TCE_NAME = 'ГУП РК «Крымтеплокоммунэнерго»'

UPDATE_INTERVAL = timedelta(minutes=30)
