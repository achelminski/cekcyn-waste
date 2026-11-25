"""Zestawianie dni wywozu z zapisanego stanu (harmonogramy roczne + gabaryty). Bez Home Assistant."""

from __future__ import annotations

from datetime import date

BULKY = "gabaryty"
BULKY_DEADLINE = "gabaryty_zgloszenie"

# kolejność = kolejność w opisie dnia i w sensorach
LABELS: dict[str, str] = {
    "tworzywa_metale": "tworzywa sztuczne i metale",
    "papier": "papier",
    "szklo": "szkło",
    "bio": "bio",
    "zmieszane": "zmieszane",
    "popiol": "popiół",
    "tekstylia": "tekstylia i odzież",
    BULKY: "gabaryty (wielkogabarytowe, elektroodpady, opony)",
}
SHORT: dict[str, str] = {**LABELS, "tworzywa_metale": "tworzywa i metale", BULKY: "gabaryty"}

PICKUP_TYPES = list(LABELS)  # frakcje z sensorem „następny termin”


def build_days(state: dict) -> dict[date, list[str]]:
    """{data: [frakcje + ewentualnie 'gabaryty' / 'gabaryty_zgloszenie']} posortowane po dacie."""
    days: dict[date, list[str]] = {}
    for schedule in (state.get("schedules") or {}).values():
        for iso, types in (schedule.get("pickups") or {}).items():
            days.setdefault(date.fromisoformat(iso), []).extend(types)
    for item in state.get("bulky") or []:
        days.setdefault(date.fromisoformat(item["date"]), []).append(BULKY)
        if item.get("deadline"):
            days.setdefault(date.fromisoformat(item["deadline"]), []).append(BULKY_DEADLINE)
    order = {key: idx for idx, key in enumerate([*LABELS, BULKY_DEADLINE])}
    return {
        day: sorted(set(types), key=lambda t: order.get(t, 99)) for day, types in sorted(days.items())
    }


def bulky_deadline_for(state: dict, day: date) -> date | None:
    for item in state.get("bulky") or []:
        if item["date"] == day.isoformat() and item.get("deadline"):
            return date.fromisoformat(item["deadline"])
    return None


def summary(types: list[str], deadline: date | None = None) -> str:
    """Tytuł zdarzenia dnia, np. „Wywóz: zmieszane, popiół”."""
    parts = []
    pickups = [t for t in types if t in LABELS]
    if pickups:
        text = "Wywóz: " + ", ".join(SHORT[t] for t in pickups)
        if BULKY in pickups and deadline:
            text += f" (zgłoszenie do {deadline:%d.%m})"
        parts.append(text)
    if BULKY_DEADLINE in types:
        parts.append("Ostatni dzień zgłoszenia gabarytów")
    return "; ".join(parts)


def description(types: list[str]) -> str:
    lines = [f"• {LABELS[t]}" for t in types if t in LABELS]
    if lines:
        lines.append("Pojemniki/worki wystaw przed posesję do 6:00.")
    if BULKY_DEADLINE in types:
        lines.append("Zgłoszenie gabarytów: Urząd Gminy Cekcyn, pok. 26, tel. 52 334 75 57, lub u sołtysa.")
    return "\n".join(lines)


def next_date(days: dict[date, list[str]], today: date, waste_type: str | None = None) -> date | None:
    """Najbliższy dzień (dziś lub później) z daną frakcją; bez frakcji — z jakimkolwiek wywozem."""
    for day, types in days.items():
        if day < today:
            continue
        if waste_type is None:
            if any(t in LABELS for t in types):
                return day
        elif waste_type in types:
            return day
    return None
