"""Stałe integracji Cekcyn Waste (harmonogram wywozu odpadów z BIP Gminy Cekcyn)."""

DOMAIN = "cekcyn_waste"

CONF_LOCALITY = "locality"
CONF_BIP_HOST = "bip_host"
CONF_MENU_ID = "menu_id"
CONF_SCAN_INTERVAL = "scan_interval"  # godziny

DEFAULT_LOCALITY = "Krzywogoniec"
DEFAULT_BIP_HOST = "bip.cekcyn.pl"
DEFAULT_MENU_ID = 611
DEFAULT_SCAN_INTERVAL = 12
MIN_SCAN_INTERVAL = 1

STORAGE_VERSION = 1

ISSUE_MISSING = "missing_schedule"
ISSUE_PARSE = "parse_error"
ISSUE_EXTRA_FILES = "extra_files"
