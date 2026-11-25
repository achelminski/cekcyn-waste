"""Cekcyn Waste: harmonogram wywozu odpadów z BIP Gminy Cekcyn jako kalendarz i sensory."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change

from .coordinator import WasteCoordinator

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]

type WasteConfigEntry = ConfigEntry[WasteCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: WasteConfigEntry) -> bool:
    coordinator = WasteCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def _midnight(_now) -> None:
        # „następny termin” i days_until zależą od daty, nie tylko od danych z BIP
        coordinator.async_update_listeners()

    entry.async_on_unload(async_track_time_change(hass, _midnight, hour=0, minute=0, second=5))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WasteConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: WasteConfigEntry) -> None:
    await WasteCoordinator(hass, entry).async_remove_store()


async def _async_update_listener(hass: HomeAssistant, entry: WasteConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
