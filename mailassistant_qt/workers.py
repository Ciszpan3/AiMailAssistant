"""Operacje sieciowe wykonywane poza głównym wątkiem Qt."""

from __future__ import annotations

import imaplib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QStandardPaths, Signal, Slot

from .core import MODEL_NAME, OLLAMA_URL, MailClient, analyze_email, enrich_message_rows, user_error
from .semantic_search import EMBEDDING_MODEL


class MailService(QObject):
    connected = Signal(list)
    folders_ready = Signal(list)
    mailbox_ready = Signal(object)
    mailbox_refreshed = Signal(object)
    refresh_failed = Signal(str, bool)
    details_ready = Signal(str, str, list)
    attachment_saved = Signal(str)
    attachment_prepared = Signal(str, int, str)
    failed = Signal(str, str)
    busy_changed = Signal(bool, str)
    disconnected = Signal()
    diagnostic = Signal(str)
    reply_context_ready = Signal(str, object)
    sent_check_ready = Signal(str, bool)
    sent_copy_ready = Signal(str, object)
    semantic_details_ready = Signal(str, str, list)
    semantic_details_failed = Signal(str, str)
    semantic_page_ready = Signal(object)
    semantic_page_failed = Signal(str)
    document_page_ready = Signal(object)
    document_page_failed = Signal(str)
    indexed_message_ready = Signal(object, str, list)

    def __init__(self, client: MailClient | None = None, cache_root: str | None = None) -> None:
        super().__init__()
        self.client = client or MailClient()
        self._attachments: dict[str, list[dict[str, Any]]] = {}
        preferred = Path(cache_root) if cache_root else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
        try:
            preferred.mkdir(parents=True, exist_ok=True)
            self._cache_root = preferred
        except OSError:
            self._cache_root = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "MailAssistant" / "cache"
            self._cache_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._cache_root, 0o700)
        except OSError:
            pass
        # Krótkotrwałe kopie załączników z poprzedniej, przerwanej sesji nie
        # powinny pozostać na dysku. Usuwamy wyłącznie własne katalogi cache.
        try:
            resolved_root = self._cache_root.resolve()
            for child in self._cache_root.glob("mailassistant_pdf_*"):
                if child.is_dir() and child.resolve().parent == resolved_root:
                    shutil.rmtree(child, ignore_errors=True)
            for pattern in ("mailassistant_scan_*", "mailassistant_test_*"):
                for child in self._cache_root.glob(pattern):
                    if child.is_file() and child.resolve().parent == resolved_root:
                        child.unlink(missing_ok=True)
        except OSError:
            pass
        self._temp_dirs: list[Path] = []
        self._folders: list[dict[str, Any]] = []

    @Slot(str, str)
    def connect_and_load(self, address: str, password: str) -> None:
        self.busy_changed.emit(True, "Łączenie z pocztą…")
        try:
            self.client.connect(address, password)
            try:
                self._folders = self.client.list_folders()
                inbox = next((item["id"] for item in self._folders if item.get("role") == "inbox"), "INBOX")
                page = self.client.load_folder(inbox, 0, 50, "")
                role = next((item.get("role") for item in self._folders if item.get("id") == inbox), "inbox")
                for row in page.get("rows", []):
                    row["folder_role"] = role
                rows = enrich_message_rows(self.client, list(page.get("rows") or []))
                page["rows"] = rows
                self.folders_ready.emit(self._folders)
                self.mailbox_ready.emit(page)
            except (AttributeError, NotImplementedError):
                rows = enrich_message_rows(self.client, self.client.get_messages())
            self.connected.emit(rows)
        except Exception as exc:  # granica wątku: błąd wraca jako sygnał
            self.diagnostic.emit(f"Poczta: {type(exc).__name__}: {exc}")
            self.failed.emit("poczta", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    @Slot(str, int, int, str)
    def load_mailbox(self, folder: str, offset: int, limit: int, query: str) -> None:
        self.busy_changed.emit(True, "Wyszukiwanie wiadomości…" if query else "Pobieranie wiadomości…")
        try:
            result = self._load_mailbox_value(folder, offset, limit, query)
            self.mailbox_ready.emit(result)
        except Exception as exc:
            self.diagnostic.emit(f"Folder poczty: {type(exc).__name__}: {exc}")
            self.failed.emit("folder poczty", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    def _load_mailbox_value(self, folder: str, offset: int, limit: int, query: str) -> dict[str, Any]:
        """Pobiera skrzynkę w jednym miejscu dla zwykłego ładowania i odświeżania."""
        if folder == "__ALL__":
            combined: list[dict[str, Any]] = []
            total = 0
            needed = max(1, int(offset) + int(limit))
            for item in self._folders:
                if item.get("role") == "trash":
                    continue
                folder_id = str(item.get("id") or "INBOX")
                folder_rows: list[dict[str, Any]] = []
                folder_total = 0
                folder_offset = 0
                # Do poprawnego podziału wspólnej chronologii trzeba znać
                # odpowiednio długi początek każdego folderu. Pobieramy go
                # partiami, a nie całą skrzynkę naraz.
                while folder_offset < needed:
                    batch = min(100, needed - folder_offset)
                    page = self.client.load_folder(folder_id, folder_offset, batch, query)
                    current_rows = list(page.get("rows") or [])
                    folder_total = int(page.get("total") or 0)
                    for row in current_rows:
                        row["folder_role"] = item.get("role")
                        row.setdefault("folder", folder_id)
                    folder_rows.extend(enrich_message_rows(self.client, current_rows))
                    folder_offset += len(current_rows)
                    if not current_rows or folder_offset >= folder_total:
                        break
                combined.extend(folder_rows)
                total += folder_total
            combined.sort(key=lambda row: float(row.get("date_sort") or 0.0), reverse=True)
            return {
                "folder": folder, "rows": combined[offset:offset + limit], "total": total,
                "offset": offset, "limit": limit, "query": query.strip(),
            }
        result = self.client.load_folder(folder, offset, limit, query)
        role = next((item.get("role") for item in self._folders if item.get("id") == folder), "other")
        for row in result.get("rows", []):
            row["folder_role"] = role
        result["rows"] = enrich_message_rows(self.client, list(result.get("rows") or []))
        return result

    @Slot(str, int, str)
    def refresh_mailbox(self, folder: str, limit: int, query: str) -> None:
        """Pobiera aktualny początek listy bez mieszania wyniku ze zwykłą paginacją."""
        self.busy_changed.emit(True, "Sprawdzanie nowych wiadomości…")
        try:
            clear_cache = getattr(self.client, "clear_folder_cache", None)
            if callable(clear_cache):
                clear_cache(folder)
            result = self._load_mailbox_value(folder, 0, limit, query)
            self.mailbox_refreshed.emit(result)
        except Exception as exc:
            self.diagnostic.emit(f"Odświeżanie poczty: {type(exc).__name__}: {exc}")
            connection_lost = isinstance(exc, (OSError, TimeoutError, imaplib.IMAP4.abort)) or (
                isinstance(exc, RuntimeError) and "brak połączenia" in str(exc).lower()
            )
            self.refresh_failed.emit(user_error(exc), connection_lost)
        finally:
            self.busy_changed.emit(False, "")

    @Slot(str)
    def load_details(self, uid: str) -> None:
        self.busy_changed.emit(True, "Otwieranie wiadomości…")
        try:
            body, attachments = self.client.get_details(uid)
            self._attachments[uid] = attachments
            self.details_ready.emit(uid, body, [str(item.get("name", "Załącznik PDF")) for item in attachments])
        except Exception as exc:
            self.diagnostic.emit(f"Wiadomość: {type(exc).__name__}: {exc}")
            self.failed.emit("wiadomość", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    @Slot(str)
    def load_semantic_details(self, uid: str) -> None:
        """Pobiera treść do indeksu bez zmieniania stanu całego interfejsu."""
        try:
            body, attachments = self.client.get_details(uid)
            # Ta sama wiadomość może chwilę później zostać otwarta przez
            # użytkownika. Zachowujemy więc również fizyczne części MIME i ich
            # nazwy zamiast udawać, że pobrana została wyłącznie treść.
            self._attachments[uid] = attachments
            names = [str(item.get("name") or "Załącznik PDF.pdf") for item in attachments]
            self.semantic_details_ready.emit(uid, body, names)
        except Exception as exc:
            self.diagnostic.emit(f"Indeks znaczeniowy — wiadomość: {type(exc).__name__}: {exc}")
            self.semantic_details_failed.emit(uid, user_error(exc))

    @Slot(str, int, int)
    def load_semantic_page(self, folder: str, offset: int, limit: int) -> None:
        """Pobiera kolejną stronę nagłówków do stopniowego indeksowania."""
        try:
            result = self._load_mailbox_value(folder, offset, limit, "")
            self.semantic_page_ready.emit(result)
        except Exception as exc:
            self.diagnostic.emit(f"Indeks znaczeniowy — folder: {type(exc).__name__}: {exc}")
            self.semantic_page_failed.emit(user_error(exc))

    @Slot(str, int, int)
    def load_document_page(self, folder: str, offset: int, limit: int) -> None:
        """Pobiera lekką partię nagłówków do pełnego indeksu dokumentów."""
        try:
            loader = getattr(self.client, "load_document_folder", None)
            if callable(loader):
                result = loader(folder, offset, limit)
                role = next(
                    (item.get("role") for item in self._folders if item.get("id") == folder), "other"
                )
                for row in result.get("rows", []):
                    row["folder_role"] = role
                result["rows"] = enrich_message_rows(self.client, list(result.get("rows") or []))
            else:
                # Zgodność z prostymi klientami używanymi w testach i ze
                # starszymi implementacjami usługi.
                result = self._load_mailbox_value(folder, offset, limit, "")
            self.document_page_ready.emit(result)
        except Exception as exc:
            self.diagnostic.emit(f"Indeks dokumentów — folder: {type(exc).__name__}: {exc}")
            self.document_page_failed.emit(user_error(exc))

    @Slot(object)
    def open_indexed_message(self, record_value: object) -> None:
        """Otwiera wynik wyszukiwania również po ponownym uruchomieniu aplikacji."""
        self.busy_changed.emit(True, "Otwieranie znalezionej wiadomości…")
        try:
            record = dict(record_value) if isinstance(record_value, dict) else {}
            row, body, attachments = self.client.get_indexed_message(
                str(record.get("folder") or ""),
                str(record.get("uid_validity") or ""),
                str(record.get("server_uid") or ""),
            )
            row = enrich_message_rows(self.client, [row])[0]
            uid = str(row.get("uid") or "")
            self._attachments[uid] = attachments
            self.indexed_message_ready.emit(
                row, body, [str(item.get("name") or "Załącznik PDF") for item in attachments]
            )
        except Exception as exc:
            self.diagnostic.emit(f"Otwieranie wyniku wyszukiwania: {type(exc).__name__}: {exc}")
            self.failed.emit("wiadomość", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    @Slot(str, int, str)
    def save_attachment(self, uid: str, index: int, path: str) -> None:
        self.busy_changed.emit(True, "Zapisywanie załącznika…")
        try:
            attachment = self._attachments[uid][index]
            self.client.save_attachment(attachment, str(Path(path)))
            self.attachment_saved.emit(str(path))
        except Exception as exc:
            self.diagnostic.emit(f"Załącznik: {type(exc).__name__}: {exc}")
            self.failed.emit("załącznik", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    @Slot(str, int)
    def prepare_attachment(self, uid: str, index: int) -> None:
        """Tworzy krótkotrwałą kopię PDF do podglądu lub wydruku."""
        self.busy_changed.emit(True, "Przygotowywanie dokumentu…")
        try:
            if uid not in self._attachments:
                _body, attachments = self.client.get_details(uid)
                self._attachments[uid] = attachments
            attachment = self._attachments[uid][index]
            target = self._prepare_attachment_file(attachment)
            self.attachment_prepared.emit(uid, index, str(target))
        except Exception as exc:
            self.diagnostic.emit(f"Przygotowanie załącznika: {type(exc).__name__}: {exc}")
            self.failed.emit("załącznik", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    @Slot(object)
    def prepare_indexed_attachment(self, record_value: object) -> None:
        """Pobiera dokument wskazany trwałymi danymi IMAP zapisanymi w indeksie."""
        self.busy_changed.emit(True, "Pobieranie dokumentu z poczty…")
        try:
            record = dict(record_value) if isinstance(record_value, dict) else {}
            index = int(record.get("attachment_index", -1))
            attachment = self.client.get_indexed_attachment(
                str(record.get("folder") or ""),
                str(record.get("uid_validity") or ""),
                str(record.get("server_uid") or ""),
                index,
            )
            target = self._prepare_attachment_file(attachment)
            self.attachment_prepared.emit(str(record.get("mail_ref") or ""), index, str(target))
        except Exception as exc:
            self.diagnostic.emit(f"Pobieranie dokumentu z indeksu: {type(exc).__name__}: {exc}")
            self.failed.emit("załącznik", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    def _prepare_attachment_file(self, attachment: dict[str, Any]) -> Path:
        raw_name = Path(str(attachment.get("name") or "zalacznik.pdf")).name
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" .")
        if not safe_name.lower().endswith(".pdf"):
            raise RuntimeError("Wybrany załącznik nie jest dokumentem PDF.")
        safe_name = safe_name or "zalacznik.pdf"
        directory = Path(tempfile.mkdtemp(prefix="mailassistant_pdf_", dir=str(self._cache_root)))
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        self._temp_dirs.append(directory)
        target = directory / safe_name
        self.client.save_attachment(attachment, str(target))
        return target

    @Slot(str, str)
    def prepare_reply(self, uid: str, own_address: str) -> None:
        self.busy_changed.emit(True, "Przygotowywanie odpowiedzi…")
        try:
            self.reply_context_ready.emit(uid, self.client.get_reply_context(uid, own_address))
        except Exception as exc:
            self.diagnostic.emit(f"Odpowiedź: {type(exc).__name__}: {exc}")
            self.failed.emit("odpowiedź", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    @Slot(str)
    def check_sent(self, message_id: str) -> None:
        self.busy_changed.emit(True, "Sprawdzanie folderu Wysłane…")
        try:
            self.sent_check_ready.emit(message_id, self.client.message_in_sent(message_id))
        except Exception as exc:
            self.diagnostic.emit(f"Folder Wysłane: {type(exc).__name__}: {exc}")
            self.failed.emit("folder Wysłane", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    @Slot(bytes, str)
    def save_sent_copy(self, raw_message: bytes, message_id: str) -> None:
        self.busy_changed.emit(True, "Zapisywanie kopii w folderze Wysłane…")
        try:
            self.sent_copy_ready.emit(message_id, self.client.append_sent(raw_message, message_id))
        except Exception as exc:
            # Przyjęta przez SMTP wiadomość pozostaje wysłana nawet, jeśli nie
            # uda się dopisać drugiej kopii przez IMAP.
            self.diagnostic.emit(f"Kopia Wysłane: {type(exc).__name__}: {exc}")
            self.failed.emit("kopia Wysłane", user_error(exc))
        finally:
            self.busy_changed.emit(False, "")

    @Slot()
    def disconnect(self) -> None:
        try:
            self.client.disconnect()
        finally:
            self._attachments.clear()
            self._folders.clear()
            for directory in self._temp_dirs:
                shutil.rmtree(directory, ignore_errors=True)
            self._temp_dirs.clear()
            self.disconnected.emit()

    def abort(self) -> None:
        """Awaryjnie przerywa oczekiwanie na serwer IMAP."""
        self.client.abort()


class WorkerSignals(QObject):
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()
    diagnostic = Signal(str)


class FunctionWorker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        def emit(signal: Any, *values: Any) -> None:
            # Okno może zostać usunięte przez test lub zakończenie procesu, gdy
            # krótka operacja w puli właśnie dobiega końca.
            try:
                signal.emit(*values)
            except RuntimeError:
                pass
        try:
            emit(self.signals.result, self.function(*self.args))
        except Exception as exc:
            emit(self.signals.diagnostic, f"{type(exc).__name__}: {exc}")
            emit(self.signals.failed, user_error(exc))
        finally:
            emit(self.signals.finished)


def create_analysis_worker(subject: str, body: str) -> FunctionWorker:
    return FunctionWorker(analyze_email, subject, body)


def check_ollama() -> dict[str, str]:
    """Sprawdza serwer i obecność wymaganego modelu bez wysyłania maila."""
    version_url = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/version"
    tags_url = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/tags"
    try:
        with urllib.request.urlopen(urllib.request.Request(version_url, method="GET"), timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(urllib.request.Request(tags_url, method="GET"), timeout=5) as response:
            tags = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Nie można połączyć się z lokalną Ollamą. Uruchom Ollamę i spróbuj ponownie.") from exc
    models = {
        str(item.get("name", ""))
        for item in tags.get("models", [])
        if isinstance(item, dict)
    }
    if MODEL_NAME not in models:
        raise RuntimeError(
            f"Ollama działa, ale brakuje modelu {MODEL_NAME}. Uruchom: ollama pull {MODEL_NAME}"
        )
    embedding_names = {EMBEDDING_MODEL, EMBEDDING_MODEL.split(":", 1)[0], f"{EMBEDDING_MODEL.split(':', 1)[0]}:latest"}
    return {
        "version": str(payload.get("version", "uruchomiona")),
        "model": MODEL_NAME,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_ready": bool(models & embedding_names),
    }


def check_ollama_analysis() -> dict[str, str]:
    """Sprawdza nie tylko instalację, ale też krótką odpowiedź wymaganego modelu."""
    result = check_ollama()
    payload = {
        "model": MODEL_NAME,
        "stream": False,
        "messages": [
            {"role": "system", "content": "Odpowiedz wyłącznie jednym słowem: GOTOWE."},
            {"role": "user", "content": "Test działania lokalnej analizy."},
        ],
        "options": {"temperature": 0, "num_predict": 12},
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            value = json.loads(response.read().decode("utf-8"))
        content = str((value.get("message") or {}).get("content") or "").strip()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Ollama działa, ale model nie wykonał analizy testowej. Uruchom Ollamę ponownie i spróbuj jeszcze raz."
        ) from exc
    if not content:
        raise RuntimeError("Model jest zainstalowany, ale nie zwrócił odpowiedzi testowej.")
    result["analysis"] = "gotowa"
    return result

