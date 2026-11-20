"""The Yuno Energy Heat integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from yunoheat import (
    APIConnectionError,
    AuthError,
    ConfigEntryAuthFailed as YunoHeatAuthFailed,
    YunoHeatClient,
)

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import aiohttp_client, config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_DAYS,
    CONF_TOKENS,
    DEFAULT_BACKFILL_DAYS,
    DOMAIN,
    MAX_BACKFILL_DAYS,
    SERVICE_BACKFILL_HISTORY,
)
from .coordinator import YunoHeatDataUpdateCoordinator
from .token_store import ConfigEntryTokenStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

BACKFILL_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DAYS, default=DEFAULT_BACKFILL_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_BACKFILL_DAYS)
        ),
    }
)

type YunoHeatConfigEntry = ConfigEntry[YunoHeatDataUpdateCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services."""

    async def _async_backfill(call: ServiceCall) -> ServiceResponse:
        """Re-import the account's past daily readings as statistics."""
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not entries:
            raise ServiceValidationError("No loaded Yuno Energy Heat account found")

        response: dict[str, object] = {}
        for entry in entries:
            result = await entry.runtime_data.history.async_import(call.data[ATTR_DAYS])
            response[entry.title] = {
                "days_imported": result.days,
                "first": result.first.isoformat() if result.first else None,
                "last": result.last.isoformat() if result.last else None,
                "usage_kwh": round(result.usage_kwh, 3),
                "cost_eur": round(result.cost_eur, 2),
            }
        return response

    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL_HISTORY,
        _async_backfill,
        schema=BACKFILL_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: YunoHeatConfigEntry) -> bool:
    """Set up Yuno Energy Heat from a config entry."""
    email: str = entry.data[CONF_EMAIL]
    password: str = entry.data[CONF_PASSWORD]
    session = aiohttp_client.async_get_clientsession(hass)
    token_store = ConfigEntryTokenStore(hass, entry)

    client: YunoHeatClient | None = None
    if CONF_TOKENS in entry.data:
        try:
            client = await YunoHeatClient.from_saved_tokens(
                username=email,
                password=password,
                session=session,
                token_store=token_store,
            )
        except AuthError:
            _LOGGER.debug("Saved tokens are unusable; performing a fresh login")

    if client is None:
        try:
            client = await YunoHeatClient.login(
                email, password, session=session, token_store=token_store
            )
        except YunoHeatAuthFailed as err:
            raise ConfigEntryAuthFailed(f"Invalid credentials: {err}") from err
        except (AuthError, APIConnectionError) as err:
            raise ConfigEntryNotReady(
                f"Could not connect to the Yuno Heat API: {err}"
            ) from err

    coordinator = YunoHeatDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Keep the long-term statistics current: once now (a full import on first
    # use) and again after every poll.
    @callback
    def _schedule_history_import() -> None:
        entry.async_create_background_task(
            hass, _async_import_history(coordinator), f"{DOMAIN}_history_import"
        )

    entry.async_on_unload(coordinator.async_add_listener(_schedule_history_import))
    _schedule_history_import()
    return True


async def _async_import_history(coordinator: YunoHeatDataUpdateCoordinator) -> None:
    """Import any new daily readings, logging instead of raising on failure."""
    try:
        await coordinator.history.async_import()
    except HomeAssistantError as err:
        _LOGGER.warning("Could not import Yuno heat history: %s", err)


async def async_unload_entry(hass: HomeAssistant, entry: YunoHeatConfigEntry) -> bool:
    """Unload a Yuno Energy Heat config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
