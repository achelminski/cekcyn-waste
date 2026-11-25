"""Parsowanie BIP Gminy Cekcyn (Madkom): lista artykułów, PDF z harmonogramem, ogłoszenie o gabarytach.

Czyste funkcje bez Home Assistant — testowalne na zapisanych próbkach (tests/fixtures).
"""

from __future__ import annotations

from datetime import date
import html
import io
import re
import unicodedata

# klucz frakcji -> znormalizowany prefiks nazwy wiersza w tabeli PDF
ROW_PREFIXES: dict[str, str] = {
    "tworzywa_metale": "tworzywa",
    "papier": "papier",
    "szklo": "szklo",
    "bio": "bio",
    "zmieszane": "zmieszane",
    "popiol": "popiol",
    "tekstylia": "tekstylia",
}

# Tabele dla zabudowy wielolokalowej / pojazdu małogabarytowego wymieniają te same miejscowości
# co tabele główne — bierzemy je tylko, gdy nic innego nie pasuje.
_SECONDARY_GROUP = re.compile(r"^(zabudowa wiel|nieruchomosci)")
_GROUP_MARKER = "gmina cekcyn"

_MONTHS_GENITIVE = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "wrzesnia": 9, "pazdziernika": 10, "listopada": 11, "grudnia": 12,
}


class ParseError(ValueError):
    """Treść nie ma oczekiwanej struktury albo nie zawiera miejscowości."""


def normalize(text: str) -> str:
    """Bez polskich znaków, małe litery, pojedyncze spacje (ł nie rozkłada się w NFKD)."""
    text = text.replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _contains_word(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(normalize(needle))}(?!\w)", normalize(haystack)) is not None


# --- lista artykułów -------------------------------------------------------------------------


def _article_title(article: dict) -> str:
    for field in article.get("aliasFields") or []:
        if field.get("alias") == "title" and field.get("value"):
            return field["value"]
    fields = article.get("columnFields") or []
    return fields[0].get("value", "") if fields else ""


def find_articles(list_json: dict) -> dict:
    """{'municipal': {rok: artykuł}, 'bulky': [artykuł, ...]}; artykuł = {id, title, link, year}.

    Przy kilku artykułach komunalnych na ten sam rok wygrywa pierwszy z listy (API sortuje
    malejąco po dacie publikacji, więc to najnowsza korekta).
    """
    municipal: dict[int, dict] = {}
    bulky: list[dict] = []
    for article in list_json.get("articles") or []:
        title = _article_title(article)
        norm = normalize(title)
        if "harmonogram" not in norm:
            continue
        item = {"id": int(article["id"]), "title": title.strip(), "link": article.get("link", "")}
        if m := re.search(r"komunalnych na rok (\d{4})", norm):
            item["year"] = int(m.group(1))
            municipal.setdefault(item["year"], item)
        elif "wielkogabarytow" in norm and (m := re.search(r"(\d{4})", norm)):
            item["year"] = int(m.group(1))
            bulky.append(item)
    return {"municipal": municipal, "bulky": bulky}


def pdf_attachment(article_json: dict) -> dict | None:
    """Pierwszy niewykasowany załącznik PDF artykułu."""
    for att in article_json.get("attachments") or []:
        if not att.get("deleted") and str(att.get("extension", "")).lower() == "pdf":
            return att
    return None


# --- PDF z harmonogramem komunalnym ---------------------------------------------------------


def _group_text(header: str) -> str:
    """Lista miejscowości nad tabelą: tekst po ostatnim 'GMINA CEKCYN' (bywa wielowierszowa)."""
    lines = [ln.strip() for ln in header.splitlines() if ln.strip()]
    for idx in range(len(lines) - 1, -1, -1):
        if normalize(lines[idx]) == _GROUP_MARKER:
            lines = lines[idx + 1 :]
            break
    else:
        lines = lines[-1:]
    return " ".join(lines)


def extract_tables(pdf_bytes: bytes) -> list[tuple[str, list[list[str | None]]]]:
    """[(lista miejscowości, wiersze tabeli), ...] dla wszystkich tabel w PDF."""
    import pdfplumber  # noqa: PLC0415 — ciężki import tylko tam, gdzie potrzebny (executor)

    result = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            top = 0
            for table in page.find_tables():
                header = page.crop((0, top, page.width, table.bbox[1])).extract_text() or ""
                top = table.bbox[3]
                result.append((_group_text(header), table.extract()))
    return result


def select_table(tables: list[tuple[str, list]], locality: str) -> tuple[str, list]:
    """Tabela grupy zawierającej miejscowość; tabele 'Zabudowa wielolokalowa' tylko awaryjnie."""
    hits = [t for t in tables if _contains_word(t[0], locality)]
    primary = [t for t in hits if not _SECONDARY_GROUP.match(normalize(t[0]))]
    chosen = primary or hits
    if not chosen:
        raise ParseError(f"Miejscowości „{locality}” nie ma w żadnej tabeli harmonogramu")
    if len(chosen) > 1:
        groups = "; ".join(t[0] for t in chosen)
        raise ParseError(f"Miejscowość „{locality}” pasuje do kilku tabel: {groups}")
    return chosen[0]


def table_pickups(rows: list[list[str | None]], year: int) -> dict[date, list[str]]:
    """Wiersze tabeli (nagłówek + frakcje × 12 miesięcy) -> {data: [frakcje]}."""
    found: dict[str, list[date]] = {}
    for row in rows[1:]:
        if not row or len(row) < 13:
            continue
        name = normalize(row[0] or "")
        key = next((k for k, prefix in ROW_PREFIXES.items() if name.startswith(prefix)), None)
        if key is None:
            continue
        dates = []
        for month in range(1, 13):
            for day in re.findall(r"\d{1,2}", row[month] or ""):
                try:
                    dates.append(date(year, month, int(day)))
                except ValueError as err:
                    raise ParseError(f"Niepoprawna data {day}.{month:02d}.{year} ({row[0]})") from err
        found[key] = dates

    missing = [k for k in ROW_PREFIXES if not found.get(k)]
    if missing:
        raise ParseError(f"Brak terminów dla frakcji: {', '.join(missing)}")
    if len(found["zmieszane"]) < 12:
        raise ParseError(f"Podejrzanie mało terminów odpadów zmieszanych: {len(found['zmieszane'])}")

    pickups: dict[date, list[str]] = {}
    for key in ROW_PREFIXES:  # stała kolejność frakcji w opisie dnia
        for day in found[key]:
            pickups.setdefault(day, []).append(key)
    return dict(sorted(pickups.items()))


def parse_municipal_pdf(pdf_bytes: bytes, year: int, locality: str) -> tuple[str, dict[date, list[str]]]:
    """PDF z harmonogramem na rok -> (nazwa grupy miejscowości, {data: [frakcje]})."""
    group, rows = select_table(extract_tables(pdf_bytes), locality)
    return group, table_pickups(rows, year)


# --- ogłoszenie o odbiorze gabarytów (treść HTML artykułu) --------------------------------------


def html_to_text(content: str) -> str:
    text = re.sub(r"<[^>]+>", " ", content or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_bulky_html(content: str, locality: str) -> list[dict]:
    """[{'date': date, 'deadline': date|None}] dla miejscowości z ogłoszenia o gabarytach.

    Treść ma segmenty „DD.MM.RRRR r. – Sołectwa: A, B, …”; miejscowość szukana jako całe słowo,
    bo grupy z PDF nie pokrywają się z grupami gabarytów.
    """
    text = html_to_text(content)
    deadline = None
    if m := re.search(r"do dnia (\d{1,2})\s+(\w+)\s+(\d{4})", normalize(text)):
        month = _MONTHS_GENITIVE.get(m.group(2))
        if month:
            try:
                deadline = date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                deadline = None

    marks = list(re.finditer(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s*r\.?\s*[–—-]", text))
    result = []
    for idx, mark in enumerate(marks):
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        if _contains_word(text[mark.end() : end], locality):
            day, month, year = (int(g) for g in mark.groups())
            try:
                result.append({"date": date(year, month, day), "deadline": deadline})
            except ValueError as err:
                raise ParseError(f"Niepoprawna data gabarytów {mark.group(0)}") from err
    return result
