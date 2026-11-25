"""Config flow: miejscowość + adres BIP (walidowane pobraniem i sparsowaniem harmonogramu); opcje: interwał."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import NumberSelector, NumberSelectorConfig, NumberSelectorMode
from homeassistant.util import dt as dt_util

from . import parser
from .api import BipClient
from .const import (
    CONF_BIP_HOST,
    CONF_LOCALITY,
    CONF_MENU_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_BIP_HOST,
    DEFAULT_LOCALITY,
    DEFAULT_MENU_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class WasteConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            locality = user_input[CONF_LOCALITY].strip()
            host = user_input[CONF_BIP_HOST].strip().removeprefix("https://").strip("/")
            menu_id = int(user_input[CONF_MENU_ID])
            await self.async_set_unique_id(f"{host}_{menu_id}_{parser.normalize(locality)}")
            self._abort_if_unique_id_configured()
            client = BipClient(async_get_clientsession(self.hass), host, menu_id)
            try:
                await self._validate(client, locality)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                if isinstance(err, parser.ParseError):
                    errors["base"] = "locality_not_found"
                    placeholders["error"] = str(err)
                elif isinstance(err, LookupError):
                    errors["base"] = "no_schedule"
                else:
                    _LOGGER.warning("BIP %s niedostępny: %s", host, err)
                    errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 — np. wyjątki pdfminer przy uszkodzonym PDF
                _LOGGER.exception("Nieoczekiwany błąd walidacji harmonogramu")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Odpady {locality}",
                    data={CONF_LOCALITY: locality, CONF_BIP_HOST: host, CONF_MENU_ID: menu_id},
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCALITY, default=DEFAULT_LOCALITY): str,
                vol.Required(CONF_BIP_HOST, default=DEFAULT_BIP_HOST): str,
                vol.Required(CONF_MENU_ID, default=DEFAULT_MENU_ID): vol.Coerce(int),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _validate(self, client: BipClient, locality: str) -> None:
        """Najnowszy harmonogram komunalny (rok bieżący, a jeśli go nie ma — kolejny) musi mieć miejscowość."""
        found = parser.find_articles(await client.list_articles())
        year = dt_util.now().year
        article = found["municipal"].get(year) or found["municipal"].get(year + 1)
        if article is None:
            raise _NoSchedule
        atts = parser.pdf_attachments(await client.article(article["id"]))
        if not atts:
            raise _NoSchedule
        pdfs = [await client.file(int(att["id"])) for att in atts]
        await self.hass.async_add_executor_job(
            parser.parse_municipal_pdfs, pdfs, article["year"], locality
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WasteOptionsFlow()


class _NoSchedule(LookupError, ValueError):
    """Brak artykułu z harmonogramem na bieżący/kolejny rok."""


class WasteOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])})
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL, max=168, step=1,
                        unit_of_measurement="h", mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
