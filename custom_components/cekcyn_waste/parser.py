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

# Wymagane w każdym roczniku; szkło/popiół zawsze były, ale tekstylia doszły dopiero w 2026.
REQUIRED_TYPES = ("tworzywa_metale", "papier", "bio", "zmieszane")
_DATE_MARK = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s*r\.?\s*[–—-]")

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

    Tytuły zmieniały się z latami („… KOMUNALNYCH NA ROK 2026”, „… NA 2019 ROK”, „… W 2017 ROKU”),
    więc harmonogram roczny = „harmonogram” + rok, bez „gabaryt”. Przy kilku artykułach na ten sam
    rok wygrywa pierwszy z listy (API sortuje malejąco po dacie publikacji — najnowsza korekta).
    """
    municipal: dict[int, dict] = {}
    bulky: list[dict] = []
    for article in list_json.get("articles") or []:
        title = _article_title(article)
        norm = normalize(title)
        year = re.search(r"\b(20\d{2})\b", norm)
        if "harmonogram" not in norm or year is None:
            continue
        item = {
            "id": int(article["id"]),
            "title": title.strip(),
            "link": article.get("link", ""),
            "year": int(year.group(1)),
        }
        if "gabaryt" in norm:
            bulky.append(item)
        else:
            municipal.setdefault(item["year"], item)
    return {"municipal": municipal, "bulky": bulky}


def pdf_attachments(article_json: dict) -> list[dict]:
    """Niewykasowane załączniki PDF w kolejności z BIP (późniejsze bywają korektami, np. 2020)."""
    return [
        att
        for att in article_json.get("attachments") or []
        if not att.get("deleted") and str(att.get("extension", "")).lower() == "pdf"
    ]


def attachments_key(attachments: list[dict]) -> list[list]:
    """Klucz zmiany zestawu załączników (id + rozmiar) — PDF parsowany tylko przy zmianie."""
    return [[int(att["id"]), att.get("size")] for att in attachments]


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


def _table_rows(page, table) -> list[list[str | None]]:
    """Wiersze tabeli; gdy brak kolumny z nazwami frakcji (12 kolumn — PDF 2024 nie ma obramowania
    z lewej), nazwę wiersza bierze z tekstu na lewo od tabeli na wysokości wiersza."""
    rows = table.extract()
    if not rows or max(len(r) for r in rows) != 12:
        return rows
    fixed = []
    for row, cells in zip(table.rows, rows, strict=False):
        _x0, top, _x1, bottom = row.bbox
        label = page.crop((0, top, table.bbox[0], bottom)).extract_text() or ""
        fixed.append([label.strip(), *cells])
    return fixed


def extract_tables(pdf_bytes: bytes) -> list[tuple[str, list[list[str | None]]]]:
    """[(lista miejscowości, wiersze tabeli), ...] dla wszystkich tabel w PDF."""
    import pdfplumber  # noqa: PLC0415 — ciężki import tylko tam, gdzie potrzebny (executor)

    result = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            top = 0
            for table in page.find_tables():
                # tabele obok siebie (PDF 2019) — obszar nad tabelą miałby ujemną wysokość
                if table.bbox[1] > top:
                    header = page.crop((0, top, page.width, table.bbox[1])).extract_text() or ""
                else:
                    header = ""
                top = max(top, table.bbox[3])
                result.append((_group_text(header), _table_rows(page, table)))
    return result


def select_table(
    tables: list[tuple[str, list]], locality: str, file_index: list[int] | None = None
) -> tuple[str, list]:
    """Tabela grupy zawierającej miejscowość; tabele 'Zabudowa wielolokalowa' tylko awaryjnie.

    `file_index[i]` = numer pliku tabeli i; trafienia z różnych plików rozstrzyga późniejszy
    plik (korekta dołączona do artykułu), w obrębie jednego pliku kilka trafień = błąd.
    """
    file_index = file_index or [0] * len(tables)
    hits = [(f, t) for f, t in zip(file_index, tables, strict=True) if _contains_word(t[0], locality)]
    primary = [h for h in hits if not _SECONDARY_GROUP.match(normalize(h[1][0]))]
    chosen = primary or hits
    if not chosen:
        raise ParseError(f"Miejscowości „{locality}” nie ma w żadnej tabeli harmonogramu")
    last_file = max(f for f, _ in chosen)
    chosen = [t for f, t in chosen if f == last_file]
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

    missing = [k for k in REQUIRED_TYPES if not found.get(k)]
    if missing:
        raise ParseError(f"Brak terminów dla frakcji: {', '.join(missing)}")
    if len(found["zmieszane"]) < 12:
        raise ParseError(f"Podejrzanie mało terminów odpadów zmieszanych: {len(found['zmieszane'])}")

    pickups: dict[date, list[str]] = {}
    for key in ROW_PREFIXES:  # stała kolejność frakcji w opisie dnia
        for day in found.get(key, []):
            pickups.setdefault(day, []).append(key)
    return dict(sorted(pickups.items()))


def parse_municipal_pdfs(
    pdfs: list[bytes], year: int, locality: str
) -> tuple[str, dict[date, list[str]]]:
    """PDF-y artykułu z harmonogramem na rok -> (nazwa grupy miejscowości, {data: [frakcje]})."""
    tables: list[tuple[str, list]] = []
    index: list[int] = []
    for num, pdf in enumerate(pdfs):
        found = extract_tables(pdf)
        tables += found
        index += [num] * len(found)
    group, rows = select_table(tables, locality, index)
    return group, table_pickups(rows, year)


def parse_municipal_pdf(pdf_bytes: bytes, year: int, locality: str) -> tuple[str, dict[date, list[str]]]:
    return parse_municipal_pdfs([pdf_bytes], year, locality)


# --- ogłoszenie o odbiorze gabarytów (treść HTML artykułu) --------------------------------------


def html_to_text(content: str) -> str:
    text = re.sub(r"<[^>]+>", " ", content or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def has_bulky_dates(text: str) -> bool:
    """Czy tekst ogłoszenia zawiera listę „DD.MM.RRRR r. – …” (od 2025 w treści, wcześniej w PDF)."""
    return _DATE_MARK.search(text) is not None


def pdf_text(pdf_bytes: bytes) -> str:
    """Cały tekst PDF (pusty dla skanów bez warstwy tekstowej, np. gabaryty jesień 2021)."""
    import pdfplumber  # noqa: PLC0415

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def parse_bulky_html(content: str, locality: str) -> list[dict]:
    return parse_bulky_text(html_to_text(content), locality)


def parse_bulky_text(text: str, locality: str) -> list[dict]:
    """[{'date': date, 'deadline': date|None}] dla miejscowości z ogłoszenia o gabarytach.

    Tekst ma segmenty „DD.MM.RRRR r. – Sołectwa: A, B, …” (ten sam wzorzec od 2018, w treści
    artykułu albo w PDF); miejscowość szukana jako całe słowo, bo grupy z PDF harmonogramu
    nie pokrywają się z grupami gabarytów.
    """
    text = re.sub(r"\s+", " ", text)
    marks = list(_DATE_MARK.finditer(text))
    if not marks:
        return []

    # Terminy zgłoszeń to daty słowne we wstępie („do dnia 18 marca 2026 r.”, w 2019 dwa:
    # „… do dnia 15 marca …”, „… termin zgłoszeń upływa 10 maja …”). Dla wywozu bierzemy
    # najpóźniejszy termin nie późniejszy niż dzień wywozu.
    deadlines = []
    for m in re.finditer(r"(\d{1,2}) ([a-z]+) (\d{4})", normalize(text[: marks[0].start()])):
        if month := _MONTHS_GENITIVE.get(m.group(2)):
            try:
                deadlines.append(date(int(m.group(3)), month, int(m.group(1))))
            except ValueError:
                continue

    result = []
    for idx, mark in enumerate(marks):
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        if _contains_word(text[mark.end() : end], locality):
            day, month, year = (int(g) for g in mark.groups())
            try:
                pickup = date(year, month, day)
            except ValueError as err:
                raise ParseError(f"Niepoprawna data gabarytów {mark.group(0)}") from err
            deadline = max((d for d in deadlines if d <= pickup), default=None)
            result.append({"date": pickup, "deadline": deadline})
    return result
