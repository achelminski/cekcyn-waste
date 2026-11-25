"""Sensory: następny termin każdej frakcji, najbliższy wywóz, źródło (diagnostyka)."""

from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import WasteConfigEntry, schedule
from .coordinator import WasteCoordinator
from .entity import WasteEntity

# klucz frakcji -> sufiks entity_id (polski, krótki)
OBJECT_IDS = {
    "tworzywa_metale": "tworzywa_i_metale",
    "papier": "papier",
    "szklo": "szklo",
    "bio": "bio",
    "zmieszane": "zmieszane",
    "popiol": "popiol",
    "tekstylia": "tekstylia",
    schedule.BULKY: "gabaryty",
}
ICONS = {
    "tworzywa_metale": "mdi:recycle",
    "papier": "mdi:newspaper-variant-outline",
    "szklo": "mdi:bottle-wine-outline",
    "bio": "mdi:leaf",
    "zmieszane": "mdi:trash-can-outline",
    "popiol": "mdi:fire",
    "tekstylia": "mdi:tshirt-crew-outline",
    schedule.BULKY: "mdi:sofa-outline",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: WasteConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [WasteTypeSensor(coordinator, t) for t in schedule.PICKUP_TYPES]
    entities += [NextPickupSensor(coordinator), SourceSensor(coordinator)]
    async_add_entities(entities)


def _days_until(day: date | None) -> int | None:
    return (day - dt_util.now().date()).days if day else None


class WasteTypeSensor(WasteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator: WasteCoordinator, waste_type: str) -> None:
        super().__init__(coordinator, "sensor", OBJECT_IDS[waste_type])
        self._type = waste_type
        self._attr_translation_key = waste_type
        self._attr_icon = ICONS[waste_type]

    @property
    def native_value(self) -> date | None:
        return self.coordinator.next_date(self._type)

    @property
    def extra_state_attributes(self) -> dict:
        return {"days_until": _days_until(self.native_value)}


class NextPickupSensor(WasteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATE
    _attr_translation_key = "next_pickup"
    _attr_icon = "mdi:delete-clock-outline"

    def __init__(self, coordinator: WasteCoordinator) -> None:
        super().__init__(coordinator, "sensor", "najblizszy_wywoz")

    @property
    def native_value(self) -> date | None:
        return self.coordinator.next_date()

    @property
    def extra_state_attributes(self) -> dict:
        day = self.native_value
        types = [t for t in self.coordinator.days.get(day, []) if t in schedule.LABELS] if day else []
        return {
            "types": types,
            "labels": [schedule.SHORT[t] for t in types],
            "summary": schedule.summary(types) if types else None,
            "days_until": _days_until(day),
        }


class SourceSensor(WasteEntity, SensorEntity):
    _attr_translation_key = "source"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:file-document-outline"

    def __init__(self, coordinator: WasteCoordinator) -> None:
        super().__init__(coordinator, "sensor", "zrodlo")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.source_title()

    @property
    def extra_state_attributes(self) -> dict:
        state = self.coordinator.state
        return {
            "years": sorted(state["schedules"]),
            "groups": {y: s.get("group") for y, s in state["schedules"].items()},
            "sources": {y: s["source"]["url"] for y, s in state["schedules"].items()},
            "files": {y: s["source"].get("name") for y, s in state["schedules"].items()},
            "bulky": [{"date": b["date"], "deadline": b.get("deadline")} for b in state["bulky"]],
            "last_check": state.get("last_check"),
            "last_error": state.get("last_error"),
        }
