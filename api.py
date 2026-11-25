"""Klient publicznego JSON API BIP Madkom (strona BIP to SPA — HTML nie zawiera treści)."""

from __future__ import annotations

import aiohttp

_TIMEOUT = aiohttp.ClientTimeout(total=60)


class BipClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, menu_id: int) -> None:
        self._session = session
        self.host = host
        self.menu_id = menu_id

    @property
    def base(self) -> str:
        return f"https://{self.host}"

    def article_url(self, link: str) -> str:
        return f"{self.base}/{link}"

    async def _get_json(self, path: str) -> dict:
        async with self._session.get(f"{self.base}{path}", timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def list_articles(self) -> dict:
        """Aktualne (niearchiwalne) artykuły działu, najnowsze pierwsze."""
        return await self._get_json(f"/api/menu/{self.menu_id}/articles?limit=50&offset=0&archived=0")

    async def article(self, article_id: int) -> dict:
        return await self._get_json(f"/api/articles/{article_id}")

    async def file(self, attachment_id: int) -> bytes:
        async with self._session.get(f"{self.base}/api/files/{attachment_id}", timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.read()
