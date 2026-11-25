"""Testy parsera i zestawiania dni na prawdziwych danych z BIP Cekcyn (harmonogram 2026).

Uruchomienie: python -I -m pytest tests   (wymaga pdfplumber)
Moduły ładowane po ścieżce, żeby nie importować __init__.py z Home Assistant.
"""

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
PKG = ROOT.parent / "custom_components" / "cekcyn_waste"
FIX = ROOT / "fixtures"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"cekcyn_{name}", PKG / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parser = _load("parser")
schedule = _load("schedule")


def _json(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tables():
    return parser.extract_tables((FIX / "harmonogram_2026.pdf").read_bytes())


def test_find_articles():
    found = parser.find_articles(_json("articles.json"))
    assert found["municipal"][2026]["id"] == 25511
    assert found["municipal"][2025]["id"] != 25511
    assert set(found["municipal"]) == {2023, 2024, 2025, 2026}
    assert [a["id"] for a in found["bulky"] if a["year"] == 2026] == [25601]


def test_find_articles_old_titles():
    # „NA 2019 ROK”, „W 2017 ROKU”, „… FIRMĘ "WIĄZAR" NA 2015 ROK” — też harmonogramy roczne
    found = parser.find_articles(_json("articles_archived.json"))
    assert {2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022} <= set(found["municipal"])
    assert all("gabaryt" in parser.normalize(a["title"]) for a in found["bulky"])
    assert not any("gabaryt" in parser.normalize(a["title"]) for a in found["municipal"].values())


def test_pdf_attachments():
    atts = parser.pdf_attachments(_json("article_25511.json"))
    assert parser.attachments_key(atts) == [[31360, 530533]]


def test_tables_and_group(tables):
    assert len(tables) == 23  # str. 3 ma jedną tabelę, str. 13 żadnej
    group, _ = parser.select_table(tables, "Krzywogoniec")
    assert group == "Wielkie Budziska, Krzywogoniec, Okoninek, Szczuczanek, Stary Sumin 10, 10A i 10L"


def test_krzywogoniec_2026(tables):
    _, rows = parser.select_table(tables, "Krzywogoniec")
    pickups = parser.table_pickups(rows, 2026)

    def dates_of(kind):
        return [d for d, t in pickups.items() if kind in t]

    assert dates_of("zmieszane") == [
        date(2026, m, d)
        for m, d in [(1, 27), (2, 24), (3, 24), (4, 28), (5, 26), (6, 23),
                     (7, 28), (8, 25), (9, 29), (10, 27), (11, 24), (12, 29)]
    ]
    assert [d for d in dates_of("bio") if d.month == 4] == [date(2026, 4, 3), date(2026, 4, 17), date(2026, 4, 30)]
    assert dates_of("tekstylia") == [date(2026, 4, 17), date(2026, 10, 16)]
    assert dates_of("popiol") == [date(2026, m, d) for m, d in [(1, 27), (2, 24), (3, 24), (4, 28), (10, 27), (11, 24), (12, 29)]]
    assert dates_of("szklo") == [date(2026, m, d) for m, d in [(1, 23), (3, 13), (5, 22), (7, 17), (9, 11), (11, 20)]]
    assert pickups[date(2026, 12, 3)] == ["tworzywa_metale", "papier"]
    assert pickups[date(2026, 4, 17)] == ["bio", "tekstylia"]


def _dates(pickups, kind):
    return [d.strftime("%d.%m") for d, t in pickups.items() if kind in t]


def test_2024_missing_label_column():
    # pdfplumber widzi 12 kolumn (bez nazw frakcji) — nazwy odtwarzane z tekstu na lewo od tabeli
    _, pickups = parser.parse_municipal_pdf((FIX / "harmonogram_2024.pdf").read_bytes(), 2024, "Krzywogoniec")
    assert _dates(pickups, "zmieszane") == [
        "30.01", "27.02", "26.03", "30.04", "28.05", "25.06", "23.07", "27.08", "24.09", "22.10", "26.11", "30.12"
    ]
    assert _dates(pickups, "szklo") == ["11.01", "07.03", "16.05", "11.07", "05.09", "07.11"]
    assert _dates(pickups, "tekstylia") == []  # frakcja doszła dopiero w 2026


def test_2020_two_pdfs_text_correction_ignored():
    main = (FIX / "harmonogram_2020.pdf").read_bytes()
    fix = (FIX / "harmonogram_2020_korekta.pdf").read_bytes()
    # „-” w pustych komórkach popiołu; Krzywogońca korekta nie dotyczy
    _, pickups = parser.parse_municipal_pdfs([main, fix], 2020, "Krzywogoniec")
    assert _dates(pickups, "zmieszane")[8:10] == ["09.09", "23.09"]
    assert len(_dates(pickups, "popiol")) == 7
    # korekta to zdanie bez tabeli („odbiór popiołu odbędzie się 15.04.2020”) — nie jest czytana,
    # o dodatkowym PDF informuje zgłoszenie w Naprawach (koordynator)
    assert parser.extract_tables(fix) == []
    _, both = parser.parse_municipal_pdfs([main, fix], 2020, "Wysoka")
    _, only_main = parser.parse_municipal_pdfs([main], 2020, "Wysoka")
    assert both == only_main


def test_later_file_wins():
    tables = [("Wysoka, Iwiec", ["stara"]), ("Wysoka, Iwiec", ["korekta"]), ("Zdroje", ["inna"])]
    assert parser.select_table(tables, "Wysoka", [0, 1, 1])[1] == ["korekta"]
    with pytest.raises(parser.ParseError, match="kilku tabel"):
        parser.select_table(tables, "Wysoka", [0, 0, 1])


def test_bulky_from_pdf_two_deadlines():
    # 2019: daty w PDF, jedno ogłoszenie na marzec i maj z osobnymi terminami zgłoszeń
    text = parser.pdf_text((FIX / "gabaryty_2019_marzec.pdf").read_bytes())
    assert parser.has_bulky_dates(text)
    assert parser.parse_bulky_text(text, "Krzywogoniec") == [
        {"date": date(2019, 5, 17), "deadline": date(2019, 5, 10)}
    ]
    text = parser.pdf_text((FIX / "gabaryty_2023_marzec.pdf").read_bytes())
    assert parser.parse_bulky_text(text, "Krzywogoniec") == [
        {"date": date(2023, 3, 31), "deadline": date(2023, 3, 23)}
    ]


def test_bulky_content_without_dates():
    # 2023–2024: treść artykułu to sam tytuł — fallback na PDF rozpoznaje has_bulky_dates
    assert not parser.has_bulky_dates("HARMONOGRAM WYWOZU ODPADÓW WIELKOGABARYTOWYCH MARZEC 2023")
    assert parser.parse_bulky_html("<h1>HARMONOGRAM</h1>", "Krzywogoniec") == []


def test_wielolokalowa_used_only_as_fallback(tables):
    # Gołąbek jest w grupie głównej (str. 6) i w „Zabudowa wielolokalowa - Zalesie, Gołąbek” (str. 9)
    group, _ = parser.select_table(tables, "Gołąbek")
    assert group.startswith("Zalesie, Plaszkosz")


def test_unknown_locality(tables):
    with pytest.raises(parser.ParseError):
        parser.select_table(tables, "Nibylandia")


def test_whole_word_match(tables):
    # „Cekcyn” nie może złapać „Cekcynek” — tu i tak kilka tabel, więc oczekiwany błąd niejednoznaczności
    with pytest.raises(parser.ParseError, match="kilku tabel"):
        parser.select_table(tables, "Cekcyn")


def test_bulky():
    content = _json("article_25601.json")["content"]
    assert parser.parse_bulky_html(content, "Krzywogoniec") == [
        {"date": date(2026, 3, 26), "deadline": date(2026, 3, 18)}
    ]
    # Wielkie Budziska (ta sama grupa w PDF) ma gabaryty innego dnia
    assert parser.parse_bulky_html(content, "Wielkie Budziska")[0]["date"] == date(2026, 3, 25)
    assert parser.parse_bulky_html(content, "Nibylandia") == []


def test_build_days_and_summary():
    state = {
        "schedules": {"2026": {"pickups": {"2026-03-24": ["zmieszane", "popiol"], "2026-03-26": ["papier"]}}},
        "bulky": [{"date": "2026-03-26", "deadline": "2026-03-18"}],
    }
    days = schedule.build_days(state)
    assert list(days) == [date(2026, 3, 18), date(2026, 3, 24), date(2026, 3, 26)]
    assert days[date(2026, 3, 26)] == ["papier", "gabaryty"]
    assert schedule.summary(days[date(2026, 3, 24)]) == "Wywóz: zmieszane, popiół"
    assert schedule.summary(days[date(2026, 3, 26)], date(2026, 3, 18)) == (
        "Wywóz: papier, gabaryty (zgłoszenie do 18.03)"
    )
    assert schedule.summary(days[date(2026, 3, 18)]) == "Ostatni dzień zgłoszenia gabarytów"
    # termin zgłoszenia nie jest wywozem
    assert schedule.next_date(days, date(2026, 3, 1)) == date(2026, 3, 24)
    assert schedule.next_date(days, date(2026, 3, 25), "gabaryty") == date(2026, 3, 26)
    assert schedule.next_date(days, date(2026, 3, 27)) is None
