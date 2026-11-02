"""Lokalny, trwały indeks do wyszukiwania wiadomości po ich znaczeniu.

Moduł nie korzysta z zewnętrznych usług. Embeddingi są obliczane przez lokalną
Ollamę, a tekst i wektory trafiają do prywatnej bazy SQLite użytkownika.
"""

from __future__ import annotations

from array import array
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Iterable, Protocol

from PySide6.QtCore import QStandardPaths


EMBEDDING_MODEL = "embeddinggemma:300m"
OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embed"
INDEX_VERSION = 3
QWEN_MODEL = "qwen2.5:3b"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
MIN_SEMANTIC_SCORE = 0.30
MIN_ANCHORED_SCORE = 0.15
DIRECT_SEMANTIC_SCORE = 0.42
MAX_RESULTS = 5


class SemanticSearchError(RuntimeError):
    """Błąd, który można bezpiecznie opisać w interfejsie."""


class SemanticUnavailableError(SemanticSearchError):
    """Lokalny model embeddingowy nie jest obecnie dostępny."""


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SearchVerifier(Protocol):
    def analyze_query(self, query: str) -> dict[str, Any]: ...

    def verify(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]: ...


_SEARCH_STOPWORDS = {
    "a", "aby", "albo", "ale", "by", "byla", "bylo", "byl", "była", "było", "był",
    "cos", "coś", "do", "dla", "i", "jak", "jakis", "jakiś", "jakas", "jakaś", "ktora",
    "która", "ktorej", "której", "mail", "maila", "mnie", "na", "nie", "o", "ode", "od",
    "sie", "się", "ta", "tej", "to", "w", "wiadomosc", "wiadomość", "z", "ze", "że",
    "znajdz", "znajdź", "szukam", "kiedys", "kiedyś", "nazwisko",
}


def _fold(value: str) -> str:
    polish = str(value or "").casefold().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))
    normalized = unicodedata.normalize("NFKD", polish)
    return " ".join(
        "".join(character for character in normalized if not unicodedata.combining(character)).split()
    )


def _query_tokens(value: str) -> list[str]:
    return re.findall(r"[\wąćęłńóśźż-]{2,}", str(value or "").casefold(), re.UNICODE)


def _fallback_query_analysis(query: str) -> dict[str, Any]:
    quoted = [part.strip() for part in re.findall(r'[„\"]([^”\"]{2,})[”\"]', query) if part.strip()]
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", query)
    dates = re.findall(
        r"\b(?:\d{1,2}[.\-/]\d{1,2}(?:[.\-/]\d{2,4})?|\d{1,2}\s+"
        r"(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|"
        r"pazdziernika|października|listopada|grudnia)(?:\s+\d{4})?)\b",
        query, re.I,
    )
    numbers = re.findall(r"(?<!\w)\d{2,}(?!\w)", query)
    people: list[str] = []
    surname_suffix = re.compile(
        r"(?i).{3,}(?:ska|ski|cka|cki|dzka|dzki|wicz|owicz|ewicz|czyk|czak|iak|ak|ek)$"
    )
    for token in _query_tokens(query):
        if surname_suffix.fullmatch(token) and token not in people:
            people.append(token)
    for match in re.findall(r"(?<![.!?]\s)(?<!^)\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]{2,}\b", query):
        if match.casefold() not in {value.casefold() for value in people}:
            people.append(match)
    return {
        "people": people,
        "emails": emails,
        "dates": dates,
        "numbers": numbers,
        "quoted_phrases": quoted,
        "intent": normalize_text(query),
    }


def _sanitize_analysis(query: str, value: object) -> dict[str, Any]:
    fallback = _fallback_query_analysis(query)
    source = value if isinstance(value, dict) else {}
    folded_query = _fold(query)
    result: dict[str, Any] = {}
    for key in ("people", "emails", "dates", "numbers", "quoted_phrases"):
        items = source.get(key) if isinstance(source.get(key), list) else fallback[key]
        cleaned: list[str] = []
        for item in items:
            text = normalize_text(str(item))[:120]
            # Model nie może dopisać osoby ani daty, której użytkownik nie podał.
            parts = [part for part in _fold(text).split() if len(part) > 1]
            if text and parts and all(part in folded_query for part in parts):
                cleaned.append(text)
        result[key] = list(dict.fromkeys(cleaned))
    result["intent"] = normalize_text(str(source.get("intent") or fallback["intent"]))[:300]
    return result


def _verification_query(query: str, analysis: dict[str, Any]) -> str:
    """Doprecyzowuje potoczny polski szyk bez zmiany danych użytkownika."""
    folded = _fold(query)
    wants = bool(re.search(r"\bchcial(?:a|em|es|by|aby|i)?\b", folded))
    asks = bool(re.search(r"\bprosil(?:a|em|es|i)?\b", folded))
    if (wants or asks) and "ode mnie" in folded:
        people = ", ".join(str(value) for value in analysis.get("people", [])) or "wskazana osoba"
        return (
            f"{query}\n"
            f"Jednoznaczne znaczenie potocznego zdania: {people} chciała lub prosiła, "
            "aby użytkownik coś zrobił, przekazał albo potwierdził. Szukany mail musi zawierać "
            "rzeczywistą prośbę, polecenie lub oczekiwaną czynność po stronie użytkownika."
        )
    return query


def _query_requires_request(query: str) -> bool:
    folded = _fold(query)
    return bool(
        ("ode mnie" in folded and re.search(r"\b(?:chcial\w*|pros\w*|oczek\w*|zad\w*)\b", folded))
        or re.search(r"\b(?:musze|musial|mam zrobic|mialem zrobic)\b", folded)
    )


def _contains_request_evidence(text: str) -> bool:
    folded = _fold(text)
    return bool(re.search(
        r"\b(?:prosz\w*|nalezy|trzeba|musisz|powin\w*|wymag\w*|"
        r"potwierdzic|potwierdz|przeslac|przeslij|odeslac|odeslij|"
        r"wykonaj|wykonac|dostarcz|dostarczyc|uzupelnij|uzupelnic|podpisz|podpisac)\b"
        r"|\bczy\s+(?:mozesz|moglbys|moglabys)\b",
        folded,
    ))


def _is_negative_request(text: str) -> bool:
    folded = _fold(text)
    return bool(re.search(
        r"\b(?:prosze\s+nie|nie\s+trzeba|nie\s+nalezy|nie\s+przesyl\w*|"
        r"nie\s+odsyl\w*|nie\s+druk\w*|nie\s+kontakt\w*)",
        folded,
    ))


def _subject_base(value: str) -> tuple[str, bool]:
    subject = normalize_text(value)
    reply = bool(re.match(r"(?i)^\s*(?:re|odp)\s*:", subject))
    base = re.sub(r"(?i)^\s*(?:(?:re|odp|fw|fwd)\s*:\s*)+", "", subject)
    return _fold(base), reply


def _validated_source_quote(proposed: str, source: str, query: str = "") -> str:
    """Zwraca wyłącznie ciągły fragment, który naprawdę występuje w mailu."""
    quote = normalize_text(proposed)[:500]
    source_normalized = normalize_text(source)
    clauses = [
        normalize_text(part).strip(" -–—:;")
        for part in re.split(r"(?<=[.!?])\s+|\s+\d+[.)]\s+|[;]\s+", quote)
    ]
    valid = [part for part in clauses if len(part) >= 8 and _fold(part) in _fold(source_normalized)]
    query_stems = {
        _fold(token)[:4] for token in _query_tokens(query)
        if token not in _SEARCH_STOPWORDS and len(_fold(token)) >= 4
    }
    if len(quote) >= 8 and _fold(quote) in _fold(source_normalized):
        return quote[:300]
    if valid:
        if query_stems:
            best_valid = max(
                ((sum(1 for stem in query_stems if stem in _fold(part)), len(part), part) for part in valid),
                default=(0, 0, ""),
            )
            if best_valid[0] > 0:
                return best_valid[2][:300]
        return max(valid, key=len)[:300]
    source_parts = [
        normalize_text(part).strip(" -–—:;,")
        for part in re.split(r"(?<=[.!?])\s+|\n+", source_normalized)
        if len(normalize_text(part).strip()) >= 8
    ]
    if query_stems:
        scored = [
            (sum(1 for stem in query_stems if stem in _fold(part)), len(part), part)
            for part in source_parts
        ]
        best = max(scored, default=(0, 0, ""))
        if best[0] > 0:
            return best[2][:300]
    return ""


def _request_evidence_quote(source: str, query: str = "") -> str:
    """Wybiera prawdziwe, dodatnie zdanie zawierające prośbę lub obowiązek."""
    parts = [
        normalize_text(part).strip(" -–—:;,")
        for part in re.split(r"(?<=[.!?])\s+|\n+", normalize_text(source))
        if _contains_request_evidence(part) and not _is_negative_request(part)
    ]
    if not parts:
        return ""
    stems = {
        _fold(token)[:5] for token in _query_tokens(query)
        if token not in _SEARCH_STOPWORDS and len(_fold(token)) >= 4
    }
    scored = [
        (sum(1 for stem in stems if stem in _fold(part)), -index, -len(part), part)
        for index, part in enumerate(parts)
    ]
    return max(scored, default=(0, 0, 0, ""))[3][:300]


class OllamaSearchVerifier:
    """Ścisła kontrola krótkiej listy kandydatów przez lokalny model Qwen."""

    def __init__(self, model: str = QWEN_MODEL, url: str = OLLAMA_CHAT_URL, timeout: int = 45) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout

    def _chat_json(self, system: str, user: str, num_predict: int = 500) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": 0, "num_predict": num_predict},
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                outer = json.loads(response.read().decode("utf-8"))
            content = str((outer.get("message") or {}).get("content") or "{}")
            result = json.loads(content)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SemanticUnavailableError("Nie można teraz wykonać dokładnej kontroli wyników przez lokalny model.") from exc
        if not isinstance(result, dict):
            raise SemanticSearchError("Lokalny model zwrócił nieprawidłową kontrolę wyników.")
        return result

    def analyze_query(self, query: str) -> dict[str, Any]:
        result = self._chat_json(
            "Rozpoznaj wyłącznie konkrety jawnie podane w polskim opisie szukanego maila. "
            "Nie dopowiadaj danych. Zwróć JSON: people, emails, dates, numbers, quoted_phrases "
            "(tablice napisów) oraz intent (krótki sens zapytania).",
            query,
            220,
        )
        return _sanitize_analysis(query, result)

    def verify(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        compact = [
            {
                "id": str(item.get("message_key") or ""),
                "sender": str(item.get("sender") or ""),
                "subject": str(item.get("subject") or ""),
                "date": str(item.get("message_date") or ""),
                "text": str(item.get("verification_text") or "")[:2200],
            }
            for item in candidates[:4]
        ]
        result = self._chat_json(
            "Oceniasz, czy rzeczywiste maile odpowiadają zapytaniu użytkownika. Bądź rygorystyczny. "
            "match=true tylko gdy mail potwierdza cały sens zapytania, w tym osobę i żądaną czynność. "
            "Oceń KAŻDEGO kandydata osobno i zwróć osobny wpis dla każdego id; wiele wyników może mieć match=true. "
            "Dla match=true podaj DOSŁOWNY quote skopiowany z text, maksymalnie 140 znaków. "
            "Reason ma mieć maksymalnie 100 znaków. Nie przepisuj całej wiadomości i nie zgaduj. "
            "Zwróć JSON: {\"results\":[{\"id\":\"...\",\"match\":true/false,"
            "\"quote\":\"...\",\"reason\":\"krótkie wyjaśnienie po polsku\"}]}",
            json.dumps({"query": query, "candidates": compact}, ensure_ascii=False),
            1600,
        )
        rows = result.get("results") if isinstance(result.get("results"), list) else []
        allowed = {str(item.get("message_key") or ""): item for item in candidates}
        verified: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            identifier = str(row.get("id") or "")
            candidate = allowed.get(identifier)
            if not candidate or not bool(row.get("match")):
                continue
            quote = normalize_text(str(row.get("quote") or ""))[:500]
            source = normalize_text(str(candidate.get("verification_text") or ""))
            quote = _validated_source_quote(quote, source, query)
            if not quote:
                continue
            if _query_requires_request(query) and (
                not _contains_request_evidence(quote) or _is_negative_request(quote)
            ):
                request_quote = _request_evidence_quote(source, query)
                if not request_quote:
                    continue
                quote = request_quote
            verified[identifier] = {
                "quote": quote,
                "reason": normalize_text(str(row.get("reason") or ""))[:240],
            }
        return verified


def _private_data_dir() -> Path:
    preferred = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(preferred, 0o700)
        except OSError:
            pass
        return preferred
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "MailAssistant"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            fallback = Path(tempfile.gettempdir()) / "MailAssistant"
            fallback.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(fallback, 0o700)
        except OSError:
            pass
        return fallback


def account_key(address: str) -> str:
    return hashlib.sha256(str(address or "").strip().casefold().encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.replace("\u00a0", " ").replace("\x00", "")
    return re.sub(r"[ \t]+", " ", value).strip()


def clean_mail_text(value: str) -> str:
    """Usuwa techniczne śmieci i nadmiar cytowanej korespondencji.

    Reguły są celowo zachowawcze: lepiej pozostawić krótką stopkę niż wyciąć
    część właściwej wiadomości istotną dla wyszukiwania.
    """
    lines = normalize_text(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    quoted = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            if result and result[-1] != "":
                result.append("")
            continue
        if re.match(r"(?i)^(content-type|content-transfer-encoding|mime-version|x-[\w-]+):", line):
            continue
        if re.match(r"^--[-_=a-zA-Z0-9]{8,}$", line):
            continue
        if line.startswith(">"):
            quoted += 1
            if quoted > 2:
                continue
            line = line.lstrip("> ")
        if re.match(r"(?i)^(-{2,}\s*)?(original message|wiadomość oryginalna|from:|od:)\s*$", line):
            break
        result.append(line)
    text = "\n".join(result).strip()
    # Długie automatyczne stopki po separatorze zwykle nie opisują sensu maila.
    text = re.split(r"(?im)^--\s*$", text, maxsplit=1)[0].strip()
    return text[:500_000]


def chunk_text(subject: str, body: str, size: int = 1500, overlap: int = 220) -> list[str]:
    """Dzieli wiadomość na fragmenty, preferując granice akapitów i zdań."""
    prefix = f"Temat: {normalize_text(subject)}\n" if subject else ""
    text = clean_mail_text(body)
    if not text:
        text = normalize_text(subject)
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= size:
            units.append(paragraph)
            continue
        units.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+", paragraph) if part.strip())
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > size:
            starts = range(0, len(unit), max(1, size - overlap))
            pieces = [unit[start:start + size] for start in starts]
        else:
            pieces = [unit]
        for piece in pieces:
            candidate = f"{current}\n{piece}".strip() if current else piece
            if current and len(candidate) > size:
                chunks.append((prefix + current).strip())
                tail = current[-overlap:].lstrip() if overlap else ""
                current = f"{tail} {piece}".strip()
            else:
                current = candidate
    if current:
        chunks.append((prefix + current).strip())
    return [chunk for chunk in chunks if chunk][:256]


class OllamaEmbedder:
    """Klient oficjalnego lokalnego endpointu ``/api/embed`` Ollamy."""

    def __init__(self, model: str = EMBEDDING_MODEL, url: str = OLLAMA_EMBED_URL, timeout: int = 45) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        values = [str(text)[:12_000] for text in texts if str(text).strip()]
        if not values:
            return []
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"model": self.model, "input": values, "truncate": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 404 or "not found" in detail.casefold():
                raise SemanticUnavailableError(
                    f"Brakuje lokalnego modelu {self.model}. Zainstaluj go poleceniem: ollama pull {self.model}"
                ) from exc
            raise SemanticUnavailableError("Lokalny model wyszukiwania nie odpowiedział prawidłowo.") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise SemanticUnavailableError(
                "Nie można użyć wyszukiwania po opisie. Uruchom Ollamę i spróbuj ponownie."
            ) from exc
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(values):
            raise SemanticUnavailableError("Lokalny model wyszukiwania zwrócił nieprawidłowy wynik.")
        result: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not vector:
                raise SemanticUnavailableError("Lokalny model wyszukiwania nie utworzył wektora tekstu.")
            result.append([float(value) for value in vector])
        return result


def _pack_vector(vector: Iterable[float]) -> bytes:
    return array("f", (float(value) for value in vector)).tobytes()


def _unpack_vector(blob: bytes) -> array:
    vector = array("f")
    vector.frombytes(blob)
    return vector


def _cosine(first: Iterable[float], second: Iterable[float]) -> float:
    dot = first_norm = second_norm = 0.0
    count = 0
    for left, right in zip(first, second):
        dot += left * right
        first_norm += left * left
        second_norm += right * right
        count += 1
    if not count or first_norm <= 0 or second_norm <= 0:
        return -1.0
    return dot / math.sqrt(first_norm * second_norm)


def _fts_query(text: str) -> str:
    words = re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}", normalize_text(text), re.UNICODE)[:20]
    expressions = []
    for word in dict.fromkeys(words):
        safe = word.replace(chr(34), "")
        expressions.append(f'"{safe}"*' if len(safe) >= 4 else f'"{safe}"')
    return " OR ".join(expressions)


@dataclass(frozen=True)
class IndexUpdate:
    changed: bool
    chunks: int
    message_key: str


class SemanticMailIndex:
    """Trwały indeks wiadomości i ich fragmentów."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else _private_data_dir() / "semantic_mail_index.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _migrate(self) -> None:
        with closing(self._connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS semantic_messages (
                    account_key TEXT NOT NULL,
                    message_key TEXT NOT NULL,
                    mail_ref TEXT NOT NULL DEFAULT '',
                    folder TEXT NOT NULL DEFAULT '',
                    uid_validity TEXT NOT NULL DEFAULT '',
                    server_uid TEXT NOT NULL DEFAULT '',
                    sender TEXT NOT NULL DEFAULT '',
                    recipient TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    message_date TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT '',
                    body_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    PRIMARY KEY(account_key, message_key)
                );
                CREATE TABLE IF NOT EXISTS semantic_chunks (
                    account_key TEXT NOT NULL,
                    message_key TEXT NOT NULL,
                    chunk_no INTEGER NOT NULL,
                    chunk_text TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    dimension INTEGER NOT NULL,
                    PRIMARY KEY(account_key, message_key, chunk_no),
                    FOREIGN KEY(account_key, message_key)
                        REFERENCES semantic_messages(account_key, message_key) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS semantic_state (
                    account_key TEXT PRIMARY KEY,
                    complete INTEGER NOT NULL DEFAULT 0,
                    expected INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_semantic_folder
                    ON semantic_messages(account_key, folder);
                """
            )
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(semantic_messages)")}
            if "message_id" not in columns:
                db.execute("ALTER TABLE semantic_messages ADD COLUMN message_id TEXT NOT NULL DEFAULT ''")
            try:
                db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS semantic_fts USING fts5("
                    "account_key UNINDEXED, message_key UNINDEXED, chunk_no UNINDEXED, chunk_text, "
                    "tokenize='unicode61 remove_diacritics 2')"
                )
            except sqlite3.OperationalError:
                # Niektóre niestandardowe kompilacje SQLite nie mają FTS5.
                pass
            db.execute(f"PRAGMA user_version={INDEX_VERSION}")

    @staticmethod
    def _message_key(row: dict[str, Any]) -> str:
        identity = str(row.get("progress_id") or row.get("message_identity") or "").strip()
        if not identity:
            identity = "|".join(str(row.get(key) or "") for key in ("folder", "uid_validity", "server_uid", "uid"))
        if not identity.strip("|"):
            raise ValueError("Brak trwałego identyfikatora wiadomości.")
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def needs_indexing(self, account: str, row: dict[str, Any], body: str, model: str = EMBEDDING_MODEL) -> bool:
        message_key = self._message_key(row)
        digest = hashlib.sha256(
            (normalize_text(str(row.get("subject") or "")) + "\n" + clean_mail_text(body)).encode("utf-8")
        ).hexdigest()
        with closing(self._connect()) as db:
            existing = db.execute(
                "SELECT body_hash,model FROM semantic_messages WHERE account_key=? AND message_key=?",
                (account_key(account), message_key),
            ).fetchone()
        model_key = f"{model}|index:{INDEX_VERSION}"
        return not existing or existing["body_hash"] != digest or existing["model"] != model_key

    def upsert_message(self, account: str, row: dict[str, Any], body: str, embedder: Embedder) -> IndexUpdate:
        key = account_key(account)
        message_key = self._message_key(row)
        model_key = f"{embedder.model}|index:{INDEX_VERSION}"
        subject = normalize_text(str(row.get("subject") or ""))
        cleaned = clean_mail_text(body)
        digest = hashlib.sha256((subject + "\n" + cleaned).encode("utf-8")).hexdigest()
        with closing(self._connect()) as db:
            existing = db.execute(
                "SELECT body_hash,model FROM semantic_messages WHERE account_key=? AND message_key=?",
                (key, message_key),
            ).fetchone()
        if existing and existing["body_hash"] == digest and existing["model"] == model_key:
            return IndexUpdate(False, 0, message_key)
        chunks = chunk_text(subject, cleaned)
        if not chunks:
            return IndexUpdate(False, 0, message_key)
        vectors: list[list[float]] = []
        # Małe partie ograniczają chwilowe zużycie pamięci Ollamy i procesu UI.
        embedding_inputs = [
            f"title: {subject or 'none'} | text: "
            + re.sub(r"(?i)^Temat:[^\n]*\n", "", chunk).strip()
            for chunk in chunks
        ]
        for start in range(0, len(embedding_inputs), 8):
            vectors.extend(embedder.embed(embedding_inputs[start:start + 8]))
        if len(vectors) != len(chunks):
            raise SemanticSearchError("Nie udało się przygotować wszystkich fragmentów wiadomości.")
        now = datetime.now().isoformat(timespec="seconds")
        values = (
            key, message_key, str(row.get("uid") or ""), str(row.get("folder") or ""),
            str(row.get("uid_validity") or ""), str(row.get("server_uid") or ""),
            str(row.get("sender") or ""), str(row.get("recipient") or ""), subject,
            str(row.get("date") or ""), str(row.get("message_id") or ""), digest, model_key, now,
        )
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM semantic_chunks WHERE account_key=? AND message_key=?", (key, message_key))
            try:
                db.execute("DELETE FROM semantic_fts WHERE account_key=? AND message_key=?", (key, message_key))
            except sqlite3.OperationalError:
                pass
            db.execute(
                """INSERT INTO semantic_messages(
                    account_key,message_key,mail_ref,folder,uid_validity,server_uid,sender,recipient,
                    subject,message_date,message_id,body_hash,model,indexed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account_key,message_key) DO UPDATE SET
                    mail_ref=excluded.mail_ref,folder=excluded.folder,uid_validity=excluded.uid_validity,
                    server_uid=excluded.server_uid,sender=excluded.sender,recipient=excluded.recipient,
                    subject=excluded.subject,message_date=excluded.message_date,message_id=excluded.message_id,
                    body_hash=excluded.body_hash,
                    model=excluded.model,indexed_at=excluded.indexed_at""",
                values,
            )
            for number, (text, vector) in enumerate(zip(chunks, vectors)):
                db.execute(
                    "INSERT INTO semantic_chunks(account_key,message_key,chunk_no,chunk_text,vector,dimension) "
                    "VALUES(?,?,?,?,?,?)",
                    (key, message_key, number, text, _pack_vector(vector), len(vector)),
                )
                try:
                    searchable = (
                        f"Od: {row.get('sender') or ''} Do: {row.get('recipient') or ''} "
                        f"Data: {row.get('date') or ''} Temat: {subject}\n{text}"
                    )
                    db.execute(
                        "INSERT INTO semantic_fts(account_key,message_key,chunk_no,chunk_text) VALUES(?,?,?,?)",
                        (key, message_key, number, searchable),
                    )
                except sqlite3.OperationalError:
                    pass
            db.execute(
                "INSERT INTO semantic_state(account_key,complete,expected,updated_at) VALUES(?,0,0,?) "
                "ON CONFLICT(account_key) DO UPDATE SET complete=0,updated_at=excluded.updated_at",
                (key, now),
            )
        return IndexUpdate(True, len(chunks), message_key)

    def set_complete(self, account: str, complete: bool, expected: int = 0) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO semantic_state(account_key,complete,expected,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(account_key) DO UPDATE SET complete=excluded.complete,expected=excluded.expected,updated_at=excluded.updated_at",
                (account_key(account), 1 if complete else 0, max(0, int(expected)), datetime.now().isoformat(timespec="seconds")),
            )

    def status(self, account: str) -> dict[str, Any]:
        key = account_key(account)
        with closing(self._connect()) as db:
            count = int(db.execute(
                "SELECT COUNT(*) FROM semantic_messages WHERE account_key=?", (key,)
            ).fetchone()[0])
            state = db.execute("SELECT * FROM semantic_state WHERE account_key=?", (key,)).fetchone()
        return {
            "indexed": count,
            "complete": bool(state["complete"]) if state else False,
            "expected": int(state["expected"]) if state else 0,
            "updated_at": str(state["updated_at"]) if state else "",
        }

    def _semantic_ranking(
        self, key: str, query_vector: list[float], folder: str = "", model_key: str = "",
    ) -> tuple[list[str], dict[str, tuple[float, str]]]:
        sql = (
            "SELECT c.message_key,c.chunk_text,c.vector FROM semantic_chunks c "
            "JOIN semantic_messages m ON m.account_key=c.account_key AND m.message_key=c.message_key "
            "WHERE c.account_key=?"
        )
        params: list[Any] = [key]
        if folder:
            sql += " AND m.folder=?"
            params.append(folder)
        if model_key:
            sql += " AND m.model=?"
            params.append(model_key)
        best: dict[str, tuple[float, str]] = {}
        with closing(self._connect()) as db:
            cursor = db.execute(sql, params)
            while True:
                rows = cursor.fetchmany(256)
                if not rows:
                    break
                for row in rows:
                    vector = _unpack_vector(row["vector"])
                    score = _cosine(query_vector, vector)
                    current = best.get(row["message_key"])
                    if current is None or score > current[0]:
                        best[row["message_key"]] = (score, str(row["chunk_text"]))
        ranking = sorted(best, key=lambda message: best[message][0], reverse=True)
        return ranking, best

    def _text_ranking(self, key: str, query: str, folder: str = "") -> tuple[list[str], dict[str, str]]:
        expression = _fts_query(query)
        if not expression:
            return [], {}
        snippets: dict[str, str] = {}
        try:
            with closing(self._connect()) as db:
                sql = (
                    "SELECT f.message_key,c.chunk_text,bm25(semantic_fts) AS rank FROM semantic_fts f "
                    "JOIN semantic_messages m ON m.account_key=f.account_key AND m.message_key=f.message_key "
                    "JOIN semantic_chunks c ON c.account_key=f.account_key AND c.message_key=f.message_key "
                    "AND c.chunk_no=f.chunk_no "
                    "WHERE semantic_fts MATCH ? AND f.account_key=?"
                )
                params: list[Any] = [expression, key]
                if folder:
                    sql += " AND m.folder=?"
                    params.append(folder)
                sql += " ORDER BY rank LIMIT 300"
                rows = db.execute(sql, params).fetchall()
            ranking: list[str] = []
            for row in rows:
                message = str(row["message_key"])
                if message not in snippets:
                    ranking.append(message)
                    snippets[message] = str(row["chunk_text"])
            return ranking, snippets
        except sqlite3.OperationalError:
            # Bez FTS5 używamy lekkiego dopasowania tokenów, nie udając semantyki.
            words = {word.casefold() for word in re.findall(r"\w{2,}", query, re.UNICODE)}
            scored: dict[str, tuple[int, str]] = {}
            with closing(self._connect()) as db:
                sql = "SELECT message_key,chunk_text FROM semantic_chunks WHERE account_key=?"
                for row in db.execute(sql, (key,)):
                    chunk_words = {word.casefold() for word in re.findall(r"\w{2,}", row["chunk_text"], re.UNICODE)}
                    score = len(words & chunk_words)
                    current = scored.get(row["message_key"])
                    if score and (current is None or score > current[0]):
                        scored[row["message_key"]] = (score, str(row["chunk_text"]))
            ranking = sorted(scored, key=lambda message: scored[message][0], reverse=True)
            return ranking, {message: value[1] for message, value in scored.items()}

    @staticmethod
    def semantic_candidate_limit(total: int) -> int:
        """Do 500 maili bierze 30, a następnie rośnie łagodnie do 80."""
        count = max(0, int(total))
        if count <= 500:
            return 30
        return min(80, max(30, math.ceil(math.sqrt(count) * 0.8)))

    @staticmethod
    def _has_anchors(analysis: dict[str, Any]) -> bool:
        return any(analysis.get(key) for key in ("people", "emails", "dates", "numbers", "quoted_phrases"))

    @staticmethod
    def _anchor_form(value: str) -> str:
        return " ".join(re.sub(r"[^\w@]+", " ", _fold(value), flags=re.UNICODE).split())

    @classmethod
    def _matches_anchors(cls, text: str, analysis: dict[str, Any]) -> bool:
        haystack = f" {cls._anchor_form(text)} "
        for key in ("people", "emails", "dates", "quoted_phrases"):
            for value in analysis.get(key, []):
                anchor = cls._anchor_form(str(value))
                if not anchor or f" {anchor} " not in haystack:
                    return False
        for value in analysis.get("numbers", []):
            number = cls._anchor_form(str(value))
            if not number or not re.search(rf"(?<!\d){re.escape(number)}(?!\d)", haystack):
                return False
        return True

    def _anchor_candidates(self, key: str, analysis: dict[str, Any], folder: str = "") -> set[str]:
        anchors = [
            str(value)
            for name in ("people", "emails", "dates", "numbers", "quoted_phrases")
            for value in analysis.get(name, [])
            if str(value).strip()
        ]
        expression = _fts_query(" ".join(anchors))
        if not expression:
            return set()
        try:
            with closing(self._connect()) as db:
                sql = (
                    "SELECT DISTINCT f.message_key FROM semantic_fts f "
                    "JOIN semantic_messages m ON m.account_key=f.account_key AND m.message_key=f.message_key "
                    "WHERE semantic_fts MATCH ? AND f.account_key=?"
                )
                params: list[Any] = [expression, key]
                if folder:
                    sql += " AND m.folder=?"
                    params.append(folder)
                return {str(row[0]) for row in db.execute(sql, params).fetchall()}
        except sqlite3.OperationalError:
            return set()

    def _candidate_records(
        self, key: str, message_keys: list[str], semantic_details: dict[str, tuple[float, str]],
        analysis: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        if not message_keys:
            return {}
        placeholders = ",".join("?" for _ in message_keys)
        with closing(self._connect()) as db:
            metadata = {
                str(row["message_key"]): dict(row) for row in db.execute(
                    f"SELECT * FROM semantic_messages WHERE account_key=? AND message_key IN ({placeholders})",
                    [key, *message_keys],
                ).fetchall()
            }
            chunks: dict[str, list[str]] = {}
            for row in db.execute(
                f"SELECT message_key,chunk_text FROM semantic_chunks WHERE account_key=? "
                f"AND message_key IN ({placeholders}) ORDER BY message_key,chunk_no",
                [key, *message_keys],
            ):
                chunks.setdefault(str(row["message_key"]), []).append(str(row["chunk_text"]))
        result: dict[str, dict[str, Any]] = {}
        for message, row in metadata.items():
            values = chunks.get(message, [])
            semantic_snippet = semantic_details.get(message, (-1.0, ""))[1]
            anchor_chunks = [chunk for chunk in values if self._matches_anchors(chunk, analysis)]
            ordered_chunks = anchor_chunks + ([semantic_snippet] if semantic_snippet else []) + values
            unique_chunks = list(dict.fromkeys(chunk for chunk in ordered_chunks if chunk))
            combined = "\n\n".join(unique_chunks)
            full_text = "\n".join(
                [str(row.get("sender") or ""), str(row.get("recipient") or ""),
                 str(row.get("subject") or ""), str(row.get("message_date") or ""), combined]
            )
            row["all_text"] = full_text
            row["verification_text"] = combined[:7000]
            row["anchors_match"] = self._matches_anchors(full_text, analysis)
            result[message] = row
        return result

    @staticmethod
    def _lexical_coverage(query: str, text: str) -> float:
        tokens = {
            (_fold(token)[:5] if len(_fold(token)) >= 5 else _fold(token))
            for token in _query_tokens(query)
            if token not in _SEARCH_STOPWORDS and len(_fold(token)) >= 3
        }
        if not tokens:
            return 0.0
        haystack = {
            (token[:5] if len(token) >= 5 else token)
            for token in _query_tokens(_fold(text))
        }
        return len(tokens & haystack) / len(tokens)

    @staticmethod
    def _dedupe_key(row: dict[str, Any]) -> str:
        message_id = normalize_text(str(row.get("message_id") or "")).casefold()
        if message_id:
            return f"id:{message_id}"
        return "fallback:" + "|".join((
            str(row.get("body_hash") or ""),
            _fold(str(row.get("sender") or "")),
            _fold(str(row.get("subject") or "")),
            _fold(str(row.get("message_date") or "")),
        ))

    def search(
        self, account: str, query: str, embedder: Embedder | None = None,
        folder: str = "", limit: int = 10, allow_text_fallback: bool = True,
        verifier: SearchVerifier | None = None,
    ) -> dict[str, Any]:
        clean_query = normalize_text(query)
        if len(clean_query) < 3:
            raise SemanticSearchError("Opisz wiadomość co najmniej kilkoma słowami.")
        key = account_key(account)
        analysis = _fallback_query_analysis(clean_query)
        verification_notice = ""
        # Dokładne nazwisko, adres, data lub numer są już rozpoznane
        # deterministycznie. Nie uruchamiamy wtedy dodatkowego zapytania do
        # Qwen tylko po to, by ponownie odczytać te same konkrety.
        if verifier is not None and not self._has_anchors(analysis):
            try:
                analysis = _sanitize_analysis(clean_query, verifier.analyze_query(clean_query))
            except (SemanticSearchError, SemanticUnavailableError) as exc:
                verification_notice = str(exc)
        semantic_error = ""
        semantic_ranking: list[str] = []
        semantic_details: dict[str, tuple[float, str]] = {}
        current_model_key = ""
        if embedder is not None:
            try:
                current_model_key = f"{embedder.model}|index:{INDEX_VERSION}"
                vectors = embedder.embed([f"task: search result | query: {clean_query}"])
                if vectors:
                    semantic_ranking, semantic_details = self._semantic_ranking(
                        key, vectors[0], folder, current_model_key
                    )
            except SemanticUnavailableError as exc:
                current_model_key = ""
                semantic_error = str(exc)
                if not allow_text_fallback:
                    raise
        text_ranking, text_snippets = self._text_ranking(key, clean_query, folder)
        anchor_candidates = self._anchor_candidates(key, analysis, folder)
        status = self.status(account)
        semantic_limit = self.semantic_candidate_limit(int(status.get("indexed") or 0))
        semantic_selected = semantic_ranking[:semantic_limit]
        text_selected = text_ranking[:30]
        # Reciprocal Rank Fusion obejmuje całą skrzynkę podczas obliczania
        # podobieństwa, ale do kosztownej kontroli przekazuje adaptacyjną pulę.
        # Konkretne nazwisko, data lub adres nigdy nie są odcinane limitem.
        combined: dict[str, float] = {}
        for rank, message in enumerate(semantic_selected, 1):
            combined[message] = combined.get(message, 0.0) + 0.80 / (40 + rank)
        for rank, message in enumerate(text_selected, 1):
            combined[message] = combined.get(message, 0.0) + 0.20 / (40 + rank)
        for message in anchor_candidates:
            combined[message] = combined.get(message, 0.0) + 1.0
        ordered = sorted(combined, key=combined.get, reverse=True)
        if not ordered:
            return {
                "results": [], "semantic_available": not semantic_error,
                "semantic_error": semantic_error, "verification_notice": verification_notice, **status,
            }
        metadata = self._candidate_records(key, ordered, semantic_details, analysis)
        has_anchors = self._has_anchors(analysis)
        requires_request = _query_requires_request(clean_query)
        accepted: dict[str, dict[str, Any]] = {}
        ambiguous: list[dict[str, Any]] = []
        candidate_duplicates: set[str] = set()
        for message in ordered:
            row = metadata.get(message)
            if not row:
                continue
            if current_model_key and str(row.get("model") or "") != current_model_key:
                continue
            if has_anchors and not bool(row.get("anchors_match")):
                continue
            duplicate = self._dedupe_key(row)
            if duplicate in candidate_duplicates:
                continue
            candidate_duplicates.add(duplicate)
            semantic_score = semantic_details.get(message, (-1.0, ""))[0]
            lexical = self._lexical_coverage(clean_query, str(row.get("all_text") or ""))
            significant_count = len({
                _fold(token) for token in _query_tokens(clean_query)
                if token not in _SEARCH_STOPWORDS and len(_fold(token)) >= 3
            })
            lexical_required = 1.0 if significant_count <= 1 else 0.60
            strong_lexical = message in text_selected and lexical >= lexical_required
            row["semantic_score"] = semantic_score
            row["lexical_coverage"] = lexical
            row["message_key"] = message
            request_quote = (
                _request_evidence_quote(str(row.get("verification_text") or ""), clean_query)
                if requires_request else ""
            )
            if requires_request and not request_quote:
                continue
            if semantic_ranking:
                # Przy nazwisku, dacie lub innym konkrecie sam embedding nie
                # potwierdza jeszcze relacji opisanej przez użytkownika. Taki
                # konkret nie może jednak zniknąć tylko przez słaby embedding.
                if has_anchors:
                    if semantic_score < MIN_ANCHORED_SCORE:
                        continue
                    ambiguous.append(row)
                elif strong_lexical:
                    accepted[message] = {"verified": False, "lexical": True}
                else:
                    # Model zwraca liczby zmiennoprzecinkowe; próg oceniamy z
                    # dokładnością prezentowaną w konfiguracji, bez błędu 0.2999.
                    if round(semantic_score, 2) < MIN_SEMANTIC_SCORE:
                        continue
                    if semantic_score >= DIRECT_SEMANTIC_SCORE:
                        accepted[message] = {"verified": False}
                    else:
                        ambiguous.append(row)
            else:
                # Jawny, rygorystyczny tryb tekstowy. Nie udaje semantyki.
                required = 0.40 if has_anchors else 0.80
                if lexical >= required:
                    accepted[message] = {
                        "verified": False,
                        "lexical": True,
                        **({"quote": request_quote} if request_quote else {}),
                    }

        if ambiguous and verifier is not None:
            clarified_query = _verification_query(clean_query, analysis)
            for start in range(0, len(ambiguous), 4):
                if len(accepted) >= MAX_RESULTS:
                    break
                batch = ambiguous[start:start + 4]
                try:
                    verified = verifier.verify(clarified_query, batch)
                except (SemanticSearchError, SemanticUnavailableError) as exc:
                    verification_notice = str(exc)
                    break
                for message, evidence in verified.items():
                    if message in metadata:
                        accepted[message] = {"verified": True, **evidence}

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        requested_limit = max(1, min(MAX_RESULTS, int(limit)))
        for message in ordered:
            if message not in accepted:
                continue
            row = metadata.get(message)
            if not row:
                continue
            duplicate = self._dedupe_key(row)
            if duplicate in seen:
                continue
            seen.add(duplicate)
            semantic_score, semantic_snippet = semantic_details.get(message, (-1.0, ""))
            evidence = accepted[message]
            lexical_quote = (
                _validated_source_quote("", str(row.get("verification_text") or ""), clean_query)
                if evidence.get("lexical") else ""
            )
            snippet = str(
                evidence.get("quote") or lexical_quote or semantic_snippet or text_snippets.get(message, "")
            )
            snippet = re.sub(r"(?i)^Temat:[^\n]*\n", "", snippet).strip()
            snippet = (snippet[:360].rsplit(" ", 1)[0] + "…") if len(snippet) > 360 else snippet
            if semantic_score >= 0.50 or (evidence.get("verified") and has_anchors):
                label = "Bardzo podobna"
            else:
                label = "Może pasować"
            results.append({
                **row,
                "uid": row.get("mail_ref", ""),
                "date": row.get("message_date", ""),
                "snippet": snippet,
                "label": label,
                "explanation": str(evidence.get("reason") or "Fragment wiadomości potwierdzający dopasowanie:"),
                "rank": len(results) + 1,
            })
            if len(results) >= requested_limit:
                break
        return {
            "results": results,
            "semantic_available": not semantic_error,
            "semantic_error": semantic_error,
            "verification_notice": verification_notice,
            **status,
        }

    def text_search(self, account: str, query: str, folder: str = "", limit: int = 10) -> dict[str, Any]:
        return self.search(account, query, None, folder, limit)

    def integrity_check(self) -> bool:
        try:
            with closing(self._connect()) as db:
                return str(db.execute("PRAGMA quick_check").fetchone()[0]).casefold() == "ok"
        except sqlite3.DatabaseError:
            return False

    def rebuild(self, account: str) -> int:
        key = account_key(account)
        with closing(self._connect()) as db, db:
            count = int(db.execute(
                "SELECT COUNT(*) FROM semantic_messages WHERE account_key=?", (key,)
            ).fetchone()[0])
            try:
                db.execute("DELETE FROM semantic_fts WHERE account_key=?", (key,))
            except sqlite3.OperationalError:
                pass
            db.execute("DELETE FROM semantic_messages WHERE account_key=?", (key,))
            db.execute("DELETE FROM semantic_state WHERE account_key=?", (key,))
        return count


def evaluate_cases(index: SemanticMailIndex, account: str, cases: list[tuple[str, str]], embedder: Embedder) -> dict[str, float]:
    """Oblicza Recall@5 i MRR dla kontrolowanego zestawu zapytań."""
    hits = 0
    reciprocal = 0.0
    for query, expected_message_key in cases:
        result = index.search(account, query, embedder, limit=5)
        keys = [str(item.get("message_key")) for item in result["results"]]
        if expected_message_key in keys:
            rank = keys.index(expected_message_key) + 1
            hits += 1
            reciprocal += 1.0 / rank
    total = max(1, len(cases))
    return {"recall_at_5": hits / total, "mrr": reciprocal / total}
