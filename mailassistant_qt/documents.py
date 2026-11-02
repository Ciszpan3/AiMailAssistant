"""Lokalny indeks metadanych wiadomości, dokumentów i ich powiązań."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
import re
import shutil
import subprocess
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QSize, QStandardPaths
from PySide6.QtPdf import QPdfDocument

from .logic import MODEL, OLLAMA_URL


SCHEMA_VERSION = 4
CATEGORIES = {
    "all": "Wszystkie dokumenty",
    "received": "Otrzymane",
    "scanned": "Zeskanowane",
    "sent": "Wysłane",
    "to_sign": "Do podpisania",
    "tasks": "Powiązane z zadaniami",
    "favorites": "Ulubione",
}


def extract_pdf_text(path: str) -> str:
    """Czyta istniejącą warstwę tekstową PDF lokalnie, bez modyfikowania pliku."""
    candidate = Path(path)
    if not candidate.is_file():
        raise RuntimeError("Nie można odnaleźć dokumentu PDF.")
    document = QPdfDocument()
    error = document.load(str(candidate))
    if error != QPdfDocument.Error.None_:
        document.close()
        raise RuntimeError("Nie udało się odczytać tekstu z dokumentu PDF.")
    parts = []
    try:
        for page in range(document.pageCount()):
            selection = document.getAllText(page)
            text = selection.text() if selection is not None else ""
            if text:
                parts.append(text)
    finally:
        document.close()
    return "\n".join(parts)[:500000]


def local_ocr_available(executable: str | None = None) -> dict[str, Any]:
    program = executable or shutil.which("tesseract")
    if not program:
        return {
            "available": False,
            "reason": (
                "Ten PDF jest skanem bez zwykłej warstwy tekstowej. Do rozpoznania takiego skanu potrzebny jest "
                "lokalny program Tesseract OCR z językiem polskim. Podgląd i pozostałe funkcje nadal działają."
            ),
        }
    try:
        process = subprocess.run(
            [program, "--list-langs"], capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "reason": "Nie udało się uruchomić lokalnego Tesseract OCR."}
    languages = set((process.stdout + "\n" + process.stderr).split())
    if "pol" not in languages:
        return {
            "available": False,
            "reason": (
                "Program Tesseract jest zainstalowany, ale brakuje języka polskiego. "
                "Dodaj pakiet języka polskiego „pol” i spróbuj ponownie."
            ),
        }
    return {"available": True, "program": program, "languages": sorted(languages)}


def ocr_pdf(path: str, executable: str | None = None, temp_root: str | None = None) -> str:
    """Renderuje strony do plików tymczasowych i uruchamia lokalny OCR bez powłoki."""
    availability = local_ocr_available(executable)
    if not availability.get("available"):
        raise RuntimeError(str(availability.get("reason") or "Lokalny OCR nie jest dostępny."))
    program = str(availability["program"])
    document = QPdfDocument()
    error = document.load(str(Path(path)))
    if error != QPdfDocument.Error.None_:
        document.close()
        raise RuntimeError("Nie udało się otworzyć PDF-u do rozpoznania tekstu.")
    texts: list[str] = []
    try:
        cache_root = Path(temp_root) if temp_root else _private_data_dir() / "ocr_cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(cache_root, 0o700)
        except OSError:
            pass
        with tempfile.TemporaryDirectory(prefix="mailassistant_ocr_", dir=str(cache_root)) as directory:
            for page in range(document.pageCount()):
                image = document.render(page, QSize(2480, 3508))
                image_path = Path(directory) / f"page-{page + 1}.png"
                if image.isNull() or not image.save(str(image_path), "PNG"):
                    raise RuntimeError(f"Nie udało się przygotować strony {page + 1} do OCR.")
                process = subprocess.run(
                    [program, str(image_path), "stdout", "-l", "pol+eng"],
                    capture_output=True, text=True, timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
                )
                if process.returncode != 0:
                    raise RuntimeError(f"OCR nie rozpoznał strony {page + 1}.")
                texts.append(process.stdout)
    finally:
        document.close()
    return "\n".join(texts)[:500000]


def recognize_document_text(
    path: str, executable: str | None = None, temp_root: str | None = None
) -> dict[str, str]:
    """Najpierw czyta tekst PDF, a OCR uruchamia tylko dla prawdziwego skanu."""
    embedded = extract_pdf_text(path)
    if embedded.strip():
        return {"text": embedded, "method": "pdf_text"}
    recognized = ocr_pdf(path, executable=executable, temp_root=temp_root)
    return {"text": recognized, "method": "ocr"}


def interpret_natural_query(query: str) -> dict[str, str]:
    """Ollama zwraca wyłącznie filtry, które później wykonuje lokalny indeks."""
    clean = " ".join(str(query or "").split())[:500]
    if not clean:
        raise RuntimeError("Wpisz pytanie, np. „Znajdź podpisany skan”.")
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "category": {"type": "string", "enum": list(CATEGORIES)},
        },
        "required": ["query", "category"],
    }
    payload = {
        "model": MODEL, "stream": False, "format": schema,
        "messages": [
            {"role": "system", "content": (
                "Zamieniasz polskie pytanie na proste filtry lokalnego indeksu. Nie podawaj wyników, nazw plików "
                "ani faktów. category musi być jednym z: all, received, scanned, sent, to_sign, tasks, favorites. "
                "query ma zawierać wyłącznie istotne słowa wyszukiwania; usuń słowa typu znajdź, pokaż, dokumenty."
            )},
            {"role": "user", "content": clean},
        ],
        "options": {"temperature": 0.0, "num_predict": 120},
    }
    request = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            outer = json.loads(response.read().decode("utf-8"))
        result = json.loads(str((outer.get("message") or {}).get("content") or "{}"))
    except (urllib.error.URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Nie można teraz użyć Ollamy. Zwykłe wyszukiwanie nadal działa — wpisz nazwę, osobę albo datę."
        ) from exc
    category = str(result.get("category") or "all")
    if category not in CATEGORIES:
        category = "all"
    filter_query = re.sub(r"[\r\n]+", " ", str(result.get("query") or "")).strip()[:300]
    query_key = clean.lower()
    if "zeszłego miesiąca" in query_key or "zeszlego miesiaca" in query_key:
        today = datetime.now()
        month = today.month - 1 or 12
        year = today.year - 1 if today.month == 1 else today.year
        month_filter = f"{month:02d}.{year:04d}"
        if month_filter not in filter_query:
            filter_query = f"{filter_query} {month_filter}".strip()
    return {"query": filter_query, "category": category}


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
        fallback.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(fallback, 0o700)
        except OSError:
            pass
        return fallback


def document_identity(*parts: object) -> str:
    value = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DocumentIndex:
    """Mały, transakcyjny indeks. Nie przechowuje haseł ani binarnej treści PDF."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else _private_data_dir() / "document_index.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _migrate(self) -> None:
        with closing(self._connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    source_key TEXT NOT NULL UNIQUE,
                    account_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT 'application/pdf',
                    size INTEGER NOT NULL DEFAULT 0,
                    document_date TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    local_path TEXT NOT NULL DEFAULT '',
                    server_available INTEGER NOT NULL DEFAULT 0,
                    folder TEXT NOT NULL DEFAULT '',
                    uid_validity TEXT NOT NULL DEFAULT '',
                    server_uid TEXT NOT NULL DEFAULT '',
                    mail_ref TEXT NOT NULL DEFAULT '',
                    message_identity TEXT NOT NULL DEFAULT '',
                    attachment_index INTEGER NOT NULL DEFAULT -1,
                    sender TEXT NOT NULL DEFAULT '',
                    recipient TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    pdf_text TEXT NOT NULL DEFAULT '',
                    ocr_text TEXT NOT NULL DEFAULT '',
                    ocr_status TEXT NOT NULL DEFAULT 'not_requested'
                );
                CREATE TABLE IF NOT EXISTS document_tasks (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    task_key TEXT NOT NULL,
                    task_label TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(document_id, task_key)
                );
                CREATE TABLE IF NOT EXISTS document_categories (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    PRIMARY KEY(document_id, category)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    identity TEXT PRIMARY KEY,
                    account_key TEXT NOT NULL,
                    folder TEXT NOT NULL DEFAULT '',
                    uid_validity TEXT NOT NULL DEFAULT '',
                    server_uid TEXT NOT NULL DEFAULT '',
                    mail_ref TEXT NOT NULL DEFAULT '',
                    sender TEXT NOT NULL DEFAULT '',
                    recipient TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    message_date TEXT NOT NULL DEFAULT '',
                    body_text TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
                CREATE INDEX IF NOT EXISTS idx_documents_message ON documents(message_identity);
                CREATE INDEX IF NOT EXISTS idx_documents_name ON documents(name);
                CREATE INDEX IF NOT EXISTS idx_messages_subject ON messages(subject);
                """
            )
            self._repair_legacy_document_names(db)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @classmethod
    def _repair_legacy_document_names(cls, db: sqlite3.Connection) -> int:
        """Trwale poprawia stare nazwy, z których dawny dekoder zgubił polskie litery."""
        rows = [dict(row) for row in db.execute(
            "SELECT id,name,document_date,size,source_kind FROM documents "
            "WHERE source_kind LIKE 'imap_%' ORDER BY document_date,id"
        ).fetchall()]
        groups: list[list[dict[str, Any]]] = []
        for row in rows:
            matched = None
            for group in groups:
                reference = group[0]
                if (
                    str(reference.get("document_date") or "") == str(row.get("document_date") or "")
                    and int(reference.get("size") or 0) == int(row.get("size") or 0)
                    and cls._same_document_name(
                        str(reference.get("name") or ""), str(row.get("name") or "")
                    )
                ):
                    matched = group
                    break
            if matched is None:
                groups.append([row])
            else:
                matched.append(row)
        updates = 0
        for group in groups:
            canonical = max(
                (str(row.get("name") or "") for row in group), key=cls._filename_quality, default=""
            )
            for row in group:
                if canonical and str(row.get("name") or "") != canonical:
                    db.execute("UPDATE documents SET name=? WHERE id=?", (canonical, row["id"]))
                    updates += 1
        return updates

    @staticmethod
    def account_key(account: str) -> str:
        return hashlib.sha256(str(account or "").strip().lower().encode("utf-8")).hexdigest()

    def upsert_message(self, account: str, row: dict[str, Any], body: str = "") -> str:
        identity = str(row.get("progress_id") or row.get("message_identity") or row.get("uid") or "")
        if not identity:
            raise ValueError("Brak trwałego identyfikatora wiadomości.")
        values = (
            identity, self.account_key(account), str(row.get("folder") or ""),
            str(row.get("uid_validity") or ""), str(row.get("server_uid") or ""),
            str(row.get("uid") or ""), str(row.get("sender") or ""),
            str(row.get("recipient") or ""), str(row.get("subject") or ""),
            str(row.get("date") or ""), str(body or "")[:200000],
        )
        with closing(self._connect()) as db, db:
            db.execute(
                """INSERT INTO messages(identity,account_key,folder,uid_validity,server_uid,mail_ref,sender,recipient,subject,message_date,body_text)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(identity) DO UPDATE SET
                   folder=excluded.folder, uid_validity=excluded.uid_validity, server_uid=excluded.server_uid,
                   mail_ref=excluded.mail_ref, sender=excluded.sender, recipient=excluded.recipient,
                   subject=excluded.subject, message_date=excluded.message_date,
                   body_text=CASE WHEN excluded.body_text<>'' THEN excluded.body_text ELSE messages.body_text END""",
                values,
            )
        return identity

    def upsert_document(self, account: str, record: dict[str, Any]) -> str:
        source_key = str(record.get("source_key") or "")
        if not source_key:
            source_key = document_identity(
                self.account_key(account), record.get("source_kind"), record.get("message_identity"),
                record.get("attachment_index"), record.get("local_path"), record.get("name"),
            )
        identifier = document_identity("document", source_key)
        existing_favorite = 0
        with closing(self._connect()) as db, db:
            existing = db.execute("SELECT favorite FROM documents WHERE source_key=?", (source_key,)).fetchone()
            if existing:
                existing_favorite = int(existing["favorite"])
            values = {
                "id": identifier, "source_key": source_key, "account_key": self.account_key(account),
                "name": str(record.get("name") or "Dokument.pdf"),
                "category": str(record.get("category") or "received"),
                "mime_type": str(record.get("mime_type") or "application/pdf"),
                "size": max(0, int(record.get("size") or 0)),
                "document_date": str(record.get("document_date") or ""),
                "indexed_at": datetime.now().isoformat(timespec="seconds"),
                "source_kind": str(record.get("source_kind") or "local"),
                "local_path": str(record.get("local_path") or ""),
                "server_available": 1 if record.get("server_available") else 0,
                "folder": str(record.get("folder") or ""),
                "uid_validity": str(record.get("uid_validity") or ""),
                "server_uid": str(record.get("server_uid") or ""),
                "mail_ref": str(record.get("mail_ref") or ""),
                "message_identity": str(record.get("message_identity") or ""),
                "attachment_index": int(record.get("attachment_index", -1)),
                "sender": str(record.get("sender") or ""),
                "recipient": str(record.get("recipient") or ""),
                "subject": str(record.get("subject") or ""),
                "favorite": existing_favorite,
                "pdf_text": str(record.get("pdf_text") or "")[:500000],
                "ocr_text": str(record.get("ocr_text") or "")[:500000],
                "ocr_status": str(record.get("ocr_status") or "not_requested"),
            }
            columns = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            updates = ",".join(
                f"{column}=excluded.{column}" for column in values
                if column not in {"id", "source_key", "favorite"}
            )
            db.execute(
                f"INSERT INTO documents({columns}) VALUES({placeholders}) "
                f"ON CONFLICT(source_key) DO UPDATE SET {updates}", tuple(values.values()),
            )
            actual = db.execute("SELECT id FROM documents WHERE source_key=?", (source_key,)).fetchone()
            if actual:
                identifier = str(actual["id"])
                db.execute(
                    "INSERT OR IGNORE INTO document_categories(document_id,category) VALUES(?,?)",
                    (identifier, values["category"]),
                )
        return identifier

    def index_received_attachments(
        self, account: str, row: dict[str, Any], attachment_names: Iterable[str]
    ) -> list[str]:
        message_identity = self.upsert_message(account, row)
        ids = []
        category = "sent" if str(row.get("folder_role") or "") == "sent" else "received"
        for index, name in enumerate(attachment_names):
            source_key = document_identity(
                self.account_key(account), row.get("folder"), row.get("uid_validity"),
                row.get("server_uid"), index,
            )
            ids.append(self.upsert_document(account, {
                "source_key": source_key, "name": str(name), "category": category,
                "source_kind": "imap_sent_attachment" if category == "sent" else "imap_attachment",
                "server_available": True,
                "folder": row.get("folder"), "uid_validity": row.get("uid_validity"),
                "server_uid": row.get("server_uid"), "mail_ref": row.get("uid"),
                "message_identity": message_identity, "attachment_index": index,
                "sender": row.get("sender"), "recipient": row.get("recipient"),
                "subject": row.get("subject"), "document_date": row.get("date"),
            }))
        return ids

    def index_mail_headers(self, account: str, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Indeksuje partię nagłówków i nazw PDF w jednej transakcji SQLite."""
        account_key = self.account_key(account)
        indexed_messages = 0
        indexed_documents = 0
        now = datetime.now().isoformat(timespec="seconds")
        with closing(self._connect()) as db, db:
            for source in rows:
                row = dict(source)
                identity = str(
                    row.get("progress_id") or row.get("message_identity") or row.get("uid") or ""
                )
                if not identity:
                    continue
                db.execute(
                    """INSERT INTO messages(identity,account_key,folder,uid_validity,server_uid,mail_ref,sender,recipient,subject,message_date,body_text)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(identity) DO UPDATE SET
                       folder=excluded.folder, uid_validity=excluded.uid_validity, server_uid=excluded.server_uid,
                       mail_ref=excluded.mail_ref, sender=excluded.sender, recipient=excluded.recipient,
                       subject=excluded.subject, message_date=excluded.message_date""",
                    (
                        identity, account_key, str(row.get("folder") or ""),
                        str(row.get("uid_validity") or ""), str(row.get("server_uid") or ""),
                        str(row.get("uid") or ""), str(row.get("sender") or ""),
                        str(row.get("recipient") or ""), str(row.get("subject") or ""),
                        str(row.get("date") or ""), "",
                    ),
                )
                indexed_messages += 1
                names = row.get("attachment_names") if isinstance(row.get("attachment_names"), list) else []
                category = "sent" if str(row.get("folder_role") or "") == "sent" else "received"
                for attachment_index, name in enumerate(names):
                    source_key = document_identity(
                        account_key, row.get("folder"), row.get("uid_validity"),
                        row.get("server_uid"), attachment_index,
                    )
                    identifier = document_identity("document", source_key)
                    values = (
                        identifier, source_key, account_key, str(name or "Załącznik PDF"), category,
                        "application/pdf", 0, str(row.get("date") or ""), now,
                        "imap_sent_attachment" if category == "sent" else "imap_attachment",
                        "", 1, str(row.get("folder") or ""), str(row.get("uid_validity") or ""),
                        str(row.get("server_uid") or ""), str(row.get("uid") or ""), identity,
                        attachment_index, str(row.get("sender") or ""),
                        str(row.get("recipient") or ""), str(row.get("subject") or ""),
                        0, "", "", "not_requested",
                    )
                    db.execute(
                        """INSERT INTO documents(
                           id,source_key,account_key,name,category,mime_type,size,document_date,indexed_at,
                           source_kind,local_path,server_available,folder,uid_validity,server_uid,mail_ref,
                           message_identity,attachment_index,sender,recipient,subject,favorite,pdf_text,ocr_text,ocr_status
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(source_key) DO UPDATE SET
                           account_key=excluded.account_key,name=excluded.name,category=excluded.category,
                           document_date=excluded.document_date,indexed_at=excluded.indexed_at,
                           source_kind=excluded.source_kind,server_available=excluded.server_available,
                           folder=excluded.folder,uid_validity=excluded.uid_validity,server_uid=excluded.server_uid,
                           mail_ref=excluded.mail_ref,message_identity=excluded.message_identity,
                           attachment_index=excluded.attachment_index,sender=excluded.sender,
                           recipient=excluded.recipient,subject=excluded.subject""",
                        values,
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO document_categories(document_id,category) VALUES(?,?)",
                        (identifier, category),
                    )
                    indexed_documents += 1
        return {"messages": indexed_messages, "documents": indexed_documents}

    def add_local_document(
        self, account: str, path: str, category: str, message_identity: str = "",
        task_key: str = "", task_label: str = "", metadata: dict[str, Any] | None = None,
    ) -> str:
        candidate = Path(path).resolve()
        if not candidate.is_file():
            raise ValueError("Nie można odnaleźć dokumentu na dysku.")
        extra = dict(metadata or {})
        source_key = document_identity(self.account_key(account), "local", str(candidate).lower(), message_identity)
        identifier = self.upsert_document(account, {
            **extra, "source_key": source_key, "name": candidate.name, "category": category,
            "source_kind": "local_file", "local_path": str(candidate), "size": candidate.stat().st_size,
            "message_identity": message_identity, "server_available": False,
        })
        if task_key:
            self.link_task(identifier, task_key, task_label)
        return identifier

    def set_local_path(self, document_id: str, path: str) -> None:
        candidate = Path(path).resolve()
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE documents SET local_path=?, size=? WHERE id=?",
                (str(candidate), candidate.stat().st_size if candidate.is_file() else 0, document_id),
            )

    def find_attachment(self, mail_ref: str, attachment_index: int) -> dict[str, Any] | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM documents WHERE mail_ref=? AND attachment_index=?",
                (mail_ref, int(attachment_index)),
            ).fetchone()
            return dict(row) if row else None

    def link_task(self, document_id: str, task_key: str, label: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO document_tasks(document_id,task_key,task_label) VALUES(?,?,?) "
                "ON CONFLICT(document_id,task_key) DO UPDATE SET task_label=excluded.task_label",
                (document_id, task_key, str(label or "")),
            )

    def link_message_tasks(self, message_identity: str, tasks: Iterable[tuple[str, str]]) -> None:
        with closing(self._connect()) as db, db:
            docs = db.execute("SELECT id FROM documents WHERE message_identity=?", (message_identity,)).fetchall()
            for document in docs:
                for key, label in tasks:
                    db.execute(
                        "INSERT OR REPLACE INTO document_tasks(document_id,task_key,task_label) VALUES(?,?,?)",
                        (document["id"], str(key), str(label)),
                    )
                    if any(word in str(label).lower() for word in ("podpis", "wydruk", "wypeł")):
                        db.execute(
                            "INSERT OR IGNORE INTO document_categories(document_id,category) VALUES(?,?)",
                            (document["id"], "to_sign"),
                        )

    def search(self, account: str, query: str = "", category: str = "all", limit: int = 200) -> list[dict[str, Any]]:
        clauses = ["d.account_key=?"]
        params: list[Any] = [self.account_key(account)]
        if category in {"received", "scanned", "sent", "to_sign"}:
            clauses.append("EXISTS(SELECT 1 FROM document_categories c WHERE c.document_id=d.id AND c.category=?)")
            params.append(category)
        elif category == "favorites":
            clauses.append("d.favorite=1")
        elif category == "tasks":
            clauses.append("EXISTS(SELECT 1 FROM document_tasks t WHERE t.document_id=d.id)")
        words = [word for word in str(query or "").strip().split() if word][:10]
        for word in words:
            like = f"%{word}%"
            clauses.append(
                "(d.name LIKE ? OR d.sender LIKE ? OR d.recipient LIKE ? OR d.subject LIKE ? "
                "OR d.document_date LIKE ? OR d.pdf_text LIKE ? OR d.ocr_text LIKE ? "
                "OR EXISTS(SELECT 1 FROM document_tasks t WHERE t.document_id=d.id AND t.task_label LIKE ?))"
            )
            params.extend([like] * 8)
        sql = (
            "SELECT d.*, "
            "(SELECT GROUP_CONCAT(t.task_label, ' • ') FROM document_tasks t WHERE t.document_id=d.id) AS task_labels, "
            "(SELECT GROUP_CONCAT(c.category) FROM document_categories c WHERE c.document_id=d.id) AS source_categories "
            "FROM documents d WHERE " + " AND ".join(clauses)
            + " ORDER BY d.document_date DESC, d.indexed_at DESC LIMIT ?"
        )
        # Biblioteka może zawierać znacznie więcej niż jedną stronę. Wyższy
        # limit dotyczy wyłącznie lekkich metadanych z lokalnej bazy SQLite;
        # pliki PDF ani treści wiadomości nie są tu wczytywane do pamięci.
        requested_limit = max(1, min(50000, int(limit)))
        params.append(min(100000, requested_limit * 4))
        with closing(self._connect()) as db, db:
            rows = [dict(row) for row in db.execute(sql, params).fetchall()]
        return self._merge_logical_documents(rows)[:requested_limit]

    def search_page(
        self,
        account: str,
        query: str = "",
        category: str = "all",
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Zwraca jedną stronę unikalnych dokumentów z całej lokalnej biblioteki."""
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(100, int(limit)))
        rows = self.search(account, query, category, 50000)
        total = len(rows)
        if safe_offset >= total and total:
            safe_offset = ((total - 1) // safe_limit) * safe_limit
        return {
            "rows": rows[safe_offset:safe_offset + safe_limit],
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "query": str(query or "").strip(),
            "category": str(category or "all"),
        }

    @staticmethod
    def _merge_logical_documents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Scala wyłącznie prezentację tego samego załącznika z kilku folderów.

        Rekordy źródłowe pozostają w bazie, więc nie tracimy wiadomości ani
        kategorii. Bez skrótu treści najbezpieczniejszym dostępnym kluczem dla
        załączników IMAP jest nazwa, data wiadomości i pozycja załącznika.
        """
        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        order: list[tuple[Any, ...]] = []
        polish_name_keys: dict[tuple[str, int], list[tuple[Any, ...]]] = {}
        plain_name_keys: dict[tuple[str, int], list[tuple[Any, ...]]] = {}
        for value in rows:
            row = dict(value)
            source_kind = str(row.get("source_kind") or "")
            name = " ".join(str(row.get("name") or "").casefold().split())
            if source_kind.startswith("imap_"):
                document_date = str(row.get("document_date") or "").strip()
                size = int(row.get("size") or 0)
                key = ("imap", name, document_date, size)
                # Najczęstszy przypadek (identyczna nazwa) jest stałoczasowy.
                # Droższe porównanie naprawiające zgubione polskie litery
                # wykonujemy tylko pomiędzy nazwą z polskimi znakami i nazwą
                # bez nich, a nie pomiędzy każdą parą dokumentów z tego dnia.
                if key not in merged:
                    bucket = (document_date, size)
                    has_polish = any(character in name for character in "ąćęłńóśźż")
                    candidates = plain_name_keys.get(bucket, []) if has_polish else polish_name_keys.get(bucket, [])
                    for existing_key in candidates:
                        if DocumentIndex._same_document_name(
                            str(merged[existing_key].get("name") or ""), str(row.get("name") or "")
                        ):
                            key = existing_key
                            break
            else:
                local = str(row.get("local_path") or "").casefold()
                key = ("local", local or str(row.get("id") or ""))
            categories = {
                item for item in str(row.get("source_categories") or row.get("category") or "").split(",") if item
            }
            if key not in merged:
                source_link = str(row.get("mail_ref") or "") or (
                    f"{row.get('folder')}|{row.get('uid_validity')}|{row.get('server_uid')}"
                )
                row["_source_links"] = {source_link}
                row["source_count"] = 1
                row["source_ids"] = [str(row.get("id") or "")]
                row["source_categories"] = ",".join(sorted(categories))
                merged[key] = row
                order.append(key)
                if source_kind.startswith("imap_"):
                    bucket = (str(row.get("document_date") or "").strip(), int(row.get("size") or 0))
                    target = polish_name_keys if any(
                        character in name for character in "ąćęłńóśźż"
                    ) else plain_name_keys
                    target.setdefault(bucket, []).append(key)
                continue
            current = merged[key]
            source_link = str(row.get("mail_ref") or "") or (
                f"{row.get('folder')}|{row.get('uid_validity')}|{row.get('server_uid')}"
            )
            current.setdefault("_source_links", set()).add(source_link)
            current["source_count"] = len(current["_source_links"])
            current.setdefault("source_ids", []).append(str(row.get("id") or ""))
            combined = {
                item for item in str(current.get("source_categories") or "").split(",") if item
            } | categories
            current["source_categories"] = ",".join(sorted(combined))
            current["favorite"] = max(int(current.get("favorite") or 0), int(row.get("favorite") or 0))
            labels = [
                item.strip() for item in (
                    str(current.get("task_labels") or "").split(" • ")
                    + str(row.get("task_labels") or "").split(" • ")
                ) if item.strip()
            ]
            current["task_labels"] = " • ".join(dict.fromkeys(labels))
            if DocumentIndex._filename_quality(str(row.get("name") or "")) > DocumentIndex._filename_quality(
                str(current.get("name") or "")
            ):
                current["name"] = row.get("name")
            if row.get("local_path") and not current.get("local_path"):
                current["local_path"] = row.get("local_path")
                current["size"] = row.get("size")
        result = []
        for key in order:
            row = merged[key]
            row.pop("_source_links", None)
            result.append(row)
        return result

    @staticmethod
    def _filename_quality(name: str) -> int:
        polish = sum(1 for character in str(name).casefold() if character in "ąćęłńóśźż")
        replacements = str(name).count("�")
        return polish * 20 - replacements * 20 + len(str(name))

    @staticmethod
    def _same_document_name(first: str, second: str) -> bool:
        """Rozpoznaje również starą nazwę, z której dekoder zgubił polskie litery."""
        left = " ".join(str(first).casefold().split())
        right = " ".join(str(second).casefold().split())
        if left == right:
            return True
        translation = str.maketrans("ąćęłńóśźż", "acelnoszz")
        folded_left = "".join(
            character for character in unicodedata.normalize("NFKD", left.translate(translation))
            if not unicodedata.combining(character)
        )
        folded_right = "".join(
            character for character in unicodedata.normalize("NFKD", right.translate(translation))
            if not unicodedata.combining(character)
        )
        if folded_left == folded_right:
            return True
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        missing = []
        short_index = 0
        for character in longer:
            if short_index < len(shorter) and character == shorter[short_index]:
                short_index += 1
            else:
                missing.append(character)
        return (
            short_index == len(shorter)
            and 0 < len(missing) <= 3
            and all(character in "ąćęłńóśźż" for character in missing)
        )

    def get(self, document_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT d.*, GROUP_CONCAT(t.task_label, ' • ') AS task_labels FROM documents d "
                "LEFT JOIN document_tasks t ON t.document_id=d.id WHERE d.id=? GROUP BY d.id",
                (document_id,),
            ).fetchone()
            return dict(row) if row else None

    def toggle_favorite(self, document_id: str) -> bool:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT favorite FROM documents WHERE id=?", (document_id,)).fetchone()
            if not row:
                return False
            value = 0 if row["favorite"] else 1
            db.execute("UPDATE documents SET favorite=? WHERE id=?", (value, document_id))
            return bool(value)

    def update_text(self, document_id: str, text: str, ocr: bool = False, status: str = "complete") -> None:
        column = "ocr_text" if ocr else "pdf_text"
        with closing(self._connect()) as db, db:
            if ocr:
                db.execute(
                    f"UPDATE documents SET {column}=?, ocr_status=? WHERE id=?",
                    (str(text or "")[:500000], status, document_id),
                )
            else:
                db.execute(f"UPDATE documents SET {column}=? WHERE id=?", (str(text or "")[:500000], document_id))

    def rebuild(self, account: str, records: Iterable[dict[str, Any]]) -> int:
        """Atomowo odbudowuje wpisy podanego konta z dostarczonych rzeczywistych rekordów."""
        account_key = self.account_key(account)
        records = [dict(item) for item in records]
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM documents WHERE account_key=?", (account_key,))
            for record in records:
                source_key = str(record.get("source_key") or document_identity(json.dumps(record, sort_keys=True)))
                identifier = document_identity("document", source_key)
                db.execute(
                    """INSERT INTO documents(id,source_key,account_key,name,category,indexed_at,source_kind,local_path,size)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (identifier, source_key, account_key, str(record.get("name") or "Dokument.pdf"),
                     str(record.get("category") or "received"), datetime.now().isoformat(timespec="seconds"),
                     str(record.get("source_kind") or "rebuild"), str(record.get("local_path") or ""),
                     int(record.get("size") or 0)),
                )
                db.execute(
                    "INSERT INTO document_categories(document_id,category) VALUES(?,?)",
                    (identifier, str(record.get("category") or "received")),
                )
        return len(records)

    def repair(self, account: str) -> dict[str, Any]:
        """Sprawdza integralność bez usuwania wpisów ani dokumentów."""
        account_key = self.account_key(account)
        with closing(self._connect()) as db, db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            rows = db.execute(
                "SELECT local_path FROM documents WHERE account_key=? AND local_path<>''",
                (account_key,),
            ).fetchall()
        missing = sum(1 for row in rows if not Path(row["local_path"]).is_file())
        return {"integrity": integrity, "missing": missing}
