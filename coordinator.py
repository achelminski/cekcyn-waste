"""Koordynator: wyszukiwanie harmonogramów w BIP, parsowanie, zapis w .storage, zgłoszenia Repairs."""

from __future__ import annotations

from datetime import date, timedelta
import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import parser, schedule
from .api import BipClient
from .const import (
    CONF_BIP_HOST,
    CONF_LOCALITY,
    CONF_MENU_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_MISSING,
    ISSUE_PARSE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class WasteCoordinator(DataUpdateCoordinator[dict]):
    """Stan w .storage: harmonogramy roczne (z kluczem załącznika) i terminy gabarytów.

    PDF jest pobierany i parsowany tylko, gdy zmieni się załącznik (id lub rozmiar);
    przy błędzie sieci/parsowania zostaje ostatni poprawny zapis.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        hours = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        self.locality: str = entry.data[CONF_LOCALITY]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.locality}",
            update_interval=timedelta(hours=hours),
        )
        self.client = BipClient(
            async_get_clientsession(hass), entry.data[CONF_BIP_HOST], int(entry.data[CONF_MENU_ID])
        )
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.state: dict = {
            "schedules": {},  # "2026": {source, group, pickups: {iso: [frakcje]}}
            "bulky": [],  # [{date, deadline, article_id}]
            "list_link": None,
            "last_check": None,
            "last_error": None,
        }
        self.days: dict[date, list[str]] = {}

    async def async_load(self) -> None:
        if stored := await self._store.async_load():
            self.state.update(stored)
        self.days = schedule.build_days(self.state)

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

    # --- odświeżanie ------------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        today = dt_util.now().date()
        errors: list[str] = []
        parse_errors: list[str] = []
        listed = False
        try:
            list_json = await self.client.list_articles()
            listed = True
            self.state["list_link"] = list_json.get("actualListLink")
            found = parser.find_articles(list_json)
            for year in (today.year, today.year + 1):
                if article := found["municipal"].get(year):
                    try:
                        await self._update_year(year, article)
                    except parser.ParseError as err:
                        parse_errors.append(f"{article['title']}: {err}")
            try:
                await self._update_bulky(found["bulky"], today)
            except parser.ParseError as err:
                parse_errors.append(f"gabaryty: {err}")
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            detail = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
            errors.append(f"BIP: {detail}")

        self._prune(today)
        errors.extend(parse_errors)
        self.state["last_check"] = dt_util.now().isoformat()
        self.state["last_error"] = "; ".join(errors) or None
        if errors:
            _LOGGER.warning("Harmonogram odpadów %s: %s", self.locality, self.state["last_error"])
        self._update_issues(today, listed, parse_errors)
        self.days = schedule.build_days(self.state)
        await self._store.async_save(self.state)
        if errors and not self.state["schedules"]:
            raise UpdateFailed(self.state["last_error"])
        return self.state

    async def _update_year(self, year: int, article: dict) -> None:
        detail = await self.client.article(article["id"])
        att = parser.pdf_attachment(detail)
        if att is None:
            raise parser.ParseError("artykuł nie ma załącznika PDF")
        source = {
            "article_id": article["id"],
            "attachment_id": int(att["id"]),
            "size": att.get("size"),
            "name": att.get("name"),
            "title": article["title"],
            "url": self.client.article_url(article["link"]),
        }
        current = self.state["schedules"].get(str(year), {}).get("source", {})
        if (current.get("attachment_id"), current.get("size")) == (source["attachment_id"], source["size"]):
            return  # ten sam załącznik — bez ponownego pobierania
        pdf = await self.client.file(source["attachment_id"])
        try:
            group, pickups = await self.hass.async_add_executor_job(
                parser.parse_municipal_pdf, pdf, year, self.locality
            )
        except parser.ParseError:
            raise
        except Exception as err:  # noqa: BLE001 — pdfminer/pdfplumber mają własne wyjątki
            raise parser.ParseError(f"nie da się odczytać PDF ({type(err).__name__}: {err})") from err
        self.state["schedules"][str(year)] = {
            "source": source,
            "group": group,
            "pickups": {day.isoformat(): types for day, types in pickups.items()},
        }
        _LOGGER.info(
            "Harmonogram odpadów %s na %s: %d dni wywozu (%s, grupa: %s)",
            self.locality, year, len(pickups), source["name"], group,
        )

    async def _update_bulky(self, articles: list[dict], today: date) -> None:
        """Gabaryty z artykułów z bieżącego/kolejnego roku; treść jest krótka, więc czytana co cykl."""
        entries: list[dict] = []
        for article in articles:
            if article["year"] < today.year:
                continue
            detail = await self.client.article(article["id"])
            for item in parser.parse_bulky_html(detail.get("content", ""), self.locality):
                entries.append(
                    {
                        "date": item["date"].isoformat(),
                        "deadline": item["deadline"].isoformat() if item["deadline"] else None,
                        "article_id": article["id"],
                        "title": article["title"],
                    }
                )
        # artykuły znikające z listy (archiwizacja) nie kasują już zapisanych terminów
        fresh_ids = {a["id"] for a in articles if a["year"] >= today.year}
        kept = [b for b in self.state["bulky"] if b["article_id"] not in fresh_ids]
        self.state["bulky"] = sorted(kept + entries, key=lambda b: b["date"])

    def _prune(self, today: date) -> None:
        """Zostaw rok poprzedni, bieżący i kolejny."""
        self.state["schedules"] = {
            y: s for y, s in self.state["schedules"].items() if int(y) >= today.year - 1
        }
        cutoff = date(today.year - 1, 1, 1).isoformat()
        self.state["bulky"] = [b for b in self.state["bulky"] if b["date"] >= cutoff]

    def _update_issues(self, today: date, listed: bool, parse_errors: list[str]) -> None:
        entry_id = self.config_entry.entry_id
        missing_id = f"{ISSUE_MISSING}_{entry_id}"
        # Nowy harmonogram bywa publikowany na przełomie roku — alarm dopiero od 15 stycznia.
        if str(today.year) not in self.state["schedules"] and (today.month, today.day) >= (1, 15):
            if listed:
                ir.async_create_issue(
                    self.hass, DOMAIN, missing_id,
                    is_fixable=False, severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_MISSING,
                    translation_placeholders={"locality": self.locality, "year": str(today.year)},
                )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, missing_id)

        parse_id = f"{ISSUE_PARSE}_{entry_id}"
        if parse_errors:
            ir.async_create_issue(
                self.hass, DOMAIN, parse_id,
                is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_PARSE,
                translation_placeholders={"locality": self.locality, "error": "; ".join(parse_errors)},
            )
        elif listed:
            ir.async_delete_issue(self.hass, DOMAIN, parse_id)

    # --- odczyt dla encji -------------------------------------------------------------------

    def next_date(self, waste_type: str | None = None) -> date | None:
        return schedule.next_date(self.days, dt_util.now().date(), waste_type)

    def source_title(self) -> str | None:
        year = str(dt_util.now().year)
        src = self.state["schedules"].get(year, {}).get("source")
        return src["title"] if src else None
