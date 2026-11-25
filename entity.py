"""Wspólna baza encji (jedno urządzenie-usługa na miejscowość)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import WasteCoordinator


class WasteEntity(CoordinatorEntity[WasteCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: WasteCoordinator, platform: str, key: str | None) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        base = f"odpady_{slugify(coordinator.locality)}"
        self._attr_unique_id = f"{entry.entry_id}_{key or platform}"
        # entity_id po polsku niezależnie od języka systemu (nazwy encji są tłumaczone)
        self.entity_id = f"{platform}.{base}_{key}" if key else f"{platform}.{base}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Odpady {coordinator.locality}",
            manufacturer="Gmina Cekcyn (BIP)",
            model="Harmonogram wywozu odpadów",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.client.article_url(coordinator.state.get("list_link") or ""),
        )
