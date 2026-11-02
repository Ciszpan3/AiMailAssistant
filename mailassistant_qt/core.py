"""Publiczna warstwa logiki nowej aplikacji PySide6.

Implementacja znajduje się w niezależnym module ``logic`` bez tkinter. Testy
porównują jej wyniki z niezmienionym plikiem referencyjnym Testu 04A.1.
"""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .logic import (  # noqa: F401
    MODEL as MODEL_NAME,
    OLLAMA_URL,
    MailClient,
    analyze_email,
    analysis_schema,
    build_analysis_payload,
    decode_value,
    validate_analysis,
)


class UserFacingError(RuntimeError):
    """Bezpieczny komunikat, który można bezpośrednio pokazać użytkownikowi."""


def format_polish_datetime(value: str | None) -> str:
    """Zamienia datę ISO z analizy na czytelny zapis, bez zgadywania."""
    if not value:
        return "Nie podano terminu"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d.%m.%Y, godz. %H:%M")
    except (TypeError, ValueError):
        return str(value)


def message_date(message: Any) -> str:
    """Czytelna data wiadomości z nagłówka IMAP."""
    raw = message.get("Date") if message is not None else None
    if not raw:
        return "Data nieznana"
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.astimezone().strftime("%d.%m.%Y, %H:%M")
    except (TypeError, ValueError, OverflowError):
        return str(raw)


def enrich_message_rows(client: MailClient, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dodaje datę do wierszy zwracanych przez niezmieniony klient 04A.1."""
    result: list[dict[str, Any]] = []
    for row in rows:
        enriched = dict(row)
        uid = str(row.get("uid", ""))
        message = client.messages.get(uid)
        enriched["date"] = message_date(message)
        try:
            raw_date = message.get("Date") if message is not None else None
            enriched["date_sort"] = parsedate_to_datetime(raw_date).timestamp() if raw_date else 0.0
        except (TypeError, ValueError, OverflowError, OSError):
            enriched["date_sort"] = 0.0
        message_id = str(message.get("Message-ID", "")).strip() if message is not None else ""
        enriched["message_id"] = message_id
        uid_validity = str(enriched.get("uid_validity") or getattr(client, "uid_validity", "") or "")
        # UID jest trwały wyłącznie razem z UIDVALIDITY. Message-ID stanowi
        # dodatkowe zabezpieczenie, gdy serwer nie zwróci UIDVALIDITY.
        stable_identity = str(enriched.get("message_identity") or f"{uid_validity}|{uid}")
        enriched["progress_id"] = f"{stable_identity}|{message_id}"
        result.append(enriched)
    return result


def user_error(exc: BaseException) -> str:
    """Przekłada typowe błędy techniczne na spokojny komunikat dla użytkownika."""
    text = str(exc).strip()
    if isinstance(exc, UserFacingError):
        return text
    lowered = text.lower()
    if "authentication" in lowered or "login failed" in lowered or "credentials" in lowered:
        return "Nie udało się zalogować. Sprawdź adres e-mail i hasło do poczty."
    if "connection refused" in lowered or "urlopen error" in lowered:
        return "Nie można połączyć się z lokalną Ollamą. Uruchom Ollamę i spróbuj ponownie."
    if "nie można połączyć się z ollama" in lowered or "nie można połączyć się z lokalną ollamą" in lowered:
        return "Nie można połączyć się z lokalną Ollamą. Uruchom Ollamę i spróbuj ponownie."
    if "brakuje modelu" in lowered and "ollama" in lowered:
        return text
    if any(value in lowered for value in (
        "brakuje lokalnego modelu", "wyszukiwania po opisie", "lokalny model wyszukiwania",
        "opisz wiadomość co najmniej",
    )):
        return text
    if "wyszukać wiadomości" in lowered or "wyszukac wiadomosci" in lowered:
        return text
    if any(value in lowered for value in (
        "tesseract", "warstwy tekstowej", "języka polskiego", "jezyka polskiego",
        "nie udało się odczytać tekstu", "nie udało się otworzyć pdf-u",
        "nie znaleziono tego pdf-u", "nie udało się pobrać dokumentu",
        "serwer poczty nie zwrócił", "folder poczty został przebudowany",
        "najpierw połącz konto", "brakuje danych potrzebnych do pobrania",
    )):
        return text
    if "timed out" in lowered or "timeout" in lowered:
        return "Operacja trwała zbyt długo. Sprawdź połączenie i spróbuj ponownie."
    if "name or service" in lowered or "getaddrinfo" in lowered:
        return "Brak połączenia z siecią. Sprawdź internet i spróbuj ponownie."
    return "Wystąpił nieoczekiwany błąd. Szczegóły zapisano w diagnostyce."

