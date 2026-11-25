# Cekcyn Waste dla Home Assistant (`cekcyn_waste`)

[English](README.md)

Integracja szuka w [BIP Gminy Cekcyn](https://bip.cekcyn.pl/m,611,harmonogramy-wywozu-odpadow.html)
harmonogramu wywozu odpadów komunalnych na rok bieżący i kolejny. Z dołączonego PDF wyciąga terminy dla jednej
miejscowości, a z ogłoszeń o odbiorze gabarytów dzień zbiórki i termin zgłoszenia. Wszystko wystawia jako
kalendarz i sensory.

## Instalacja

### HACS
1. HACS → ⋮ → **Własne repozytoria** → `https://github.com/achelminski/cekcyn-waste`, typ **Integracja**.
2. Pobierz **Cekcyn Waste**, zrestartuj Home Assistant.

### Ręcznie
Skopiuj `custom_components/cekcyn_waste/` do `/config/custom_components/` i zrestartuj Home Assistant.

## Konfiguracja
**Ustawienia → Urządzenia i usługi → Dodaj integrację → Cekcyn Waste**: miejscowość (domyślnie Krzywogoniec),
adres BIP i numer działu (domyślnie `bip.cekcyn.pl`, `611`). Przy dodawaniu harmonogram jest od razu pobierany,
a integracja sprawdza, czy miejscowość w nim występuje. W opcjach ustawia się interwał sprawdzania BIP
(domyślnie 12 h). PDF pobierany jest ponownie tylko wtedy, gdy zmieni się załącznik.

## Encje (przykład dla Krzywogońca)
| Encja | Opis |
|---|---|
| `calendar.odpady_krzywogoniec` | jedno całodniowe zdarzenie na dzień, np. „Wywóz: zmieszane, popiół” |
| `sensor.odpady_krzywogoniec_{tworzywa_i_metale,papier,szklo,bio,zmieszane,popiol,tekstylia,gabaryty}` | data następnego wywozu (atrybut `days_until`) |
| `sensor.odpady_krzywogoniec_najblizszy_wywoz` | najbliższy dzień wywozu (atrybuty `types`, `labels`, `summary`, `days_until`) |
| `sensor.odpady_krzywogoniec_zrodlo` | diagnostyka: tytuł artykułu, lata, grupa miejscowości, ostatni błąd |

Gdy harmonogramu na bieżący rok brakuje (od 15 stycznia) albo nowy PDF nie daje się odczytać, w **Naprawach**
pojawia się zgłoszenie. Integracja korzysta wtedy dalej z ostatnich poprawnych danych.

## Przypomnienie dzień wcześniej o 18:00
```yaml
triggers:
  - trigger: calendar
    event: start
    entity_id: calendar.odpady_krzywogoniec
    offset: "-6:00:00"
actions:
  - action: notify.mobile_app_telefon
    data:
      title: "Odpady – jutro"
      message: "{{ trigger.calendar_event.summary }}. Wystaw do 6:00."
mode: queued
```

## Jak to działa
Strona BIP to aplikacja SPA, więc dane pochodzą z jej publicznego API:
`/api/menu/{dział}/articles` (lista artykułów), `/api/articles/{id}` (treść i załączniki), `/api/files/{id}` (PDF).
Artykuł z tytułem „… KOMUNALNYCH NA ROK RRRR” zawiera PDF z tabelami. Nad każdą tabelą jest lista miejscowości,
wiersze to frakcje, kolumny to miesiące, a w komórkach są dni miesiąca. Tabele „Zabudowa wielolokalowa” są
brane pod uwagę tylko wtedy, gdy miejscowości nie ma w żadnej tabeli głównej. Ogłoszenia
„… WIELKOGABARYTOWYCH …” mają daty w treści, w postaci „DD.MM.RRRR r. – Sołectwa: …”.

## Testy
`python -m pytest tests` (wymaga `pdfplumber`, `pytest`). Testy działają na zapisanym harmonogramie 2026.
