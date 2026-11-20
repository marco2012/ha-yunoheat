"""Constants for the Yuno Energy Heat integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "yunoheat"

MANUFACTURER: Final = "Yuno Energy"

DEFAULT_SCAN_INTERVAL: Final = timedelta(hours=24)

# Key inside ConfigEntry.data holding the TokenData.to_dict() payload.
CONF_TOKENS: Final = "tokens"

# How far back to look when querying for the most recent meter reading.
USAGE_LOOKBACK_DAYS: Final = 60

# History backfill: how far back a first import looks, and how it pages the API.
DEFAULT_BACKFILL_DAYS: Final = 730
MAX_BACKFILL_DAYS: Final = 1825
BACKFILL_PAGE_SIZE: Final = 100
BACKFILL_MAX_PAGES: Final = 100

SERVICE_BACKFILL_HISTORY: Final = "backfill_history"
ATTR_DAYS: Final = "days"
