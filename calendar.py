"""Kalendarz wywozu: jedno całodniowe zdarzenie na dzień (wszystkie frakcje w tytule)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import WasteConfigEntry, schedule
from .entity import WasteEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: WasteConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([WasteCalendar(entry.runtime_data)])


class WasteCalendar(WasteEntity, CalendarEntity):
    _attr_name = None  # = nazwa urządzenia „Odpady <miejscowość>”

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "calendar", None)

    def _event(self, day: date, types: list[str]) -> CalendarEvent:
        state = self.coordinator.state
        return CalendarEvent(
            start=day,
            end=day + timedelta(days=1),
            summary=schedule.summary(types, schedule.bulky_deadline_for(state, day)),
            description=schedule.description(types),
            location=self.coordinator.locality,
            uid=f"{self.coordinator.config_entry.entry_id}_{day.isoformat()}",
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Dzisiejsze albo najbliższe zdarzenie."""
        today = dt_util.now().date()
        for day, types in self.coordinator.days.items():
            if day >= today:
                return self._event(day, types)
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        # zdarzenie całodniowe [dzień 00:00, dzień+1 00:00) nachodzi na [start, end)
        start = dt_util.as_local(start_date).date()
        end = dt_util.as_local(end_date)
        return [
            self._event(day, types)
            for day, types in self.coordinator.days.items()
            if day >= start and dt_util.start_of_local_day(day) < end
        ]
