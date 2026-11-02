"""Szyfrowane kopie zapasowe i transakcyjne odzyskiwanie danych użytkownika."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QStandardPaths

from .storage import StorageError, atomic_write_bytes, ensure_free_space


BACKUP_MAGIC = b"MAILASSISTANT-BACKUP-1\n"
BACKUP_VERSION = 1
MAX_BACKUP_SOURCE_BYTES = 250 * 1024 * 1024
SENSITIVE_KEY = re.compile(r"(?:password|passwd|secret|token|credential|haslo|hasło)", re.I)
DRAFT_NAME = re.compile(r"^[0-9a-f]{64}\.draft$")


class BackupError(RuntimeError):
    pass


class DpapiProtector:
    """Systemowe szyfrowanie przypisane do aktualnego konta Windows."""

    def protect(self, data: bytes) -> bytes:
        if os.name != "nt":
            raise BackupError("Szyfrowane kopie zapasowe są obecnie dostępne tylko w Windows.")
        try:
            import win32crypt
            protected = win32crypt.CryptProtectData(data, "Asystent Poczty — kopia", None, None, None, 0)
            blob = protected[1] if isinstance(protected, tuple) else protected
            if not isinstance(blob, bytes):
                raise TypeError("Nieprawidłowy wynik Windows DPAPI")
            return blob
        except Exception as exc:
            raise BackupError("Windows nie zdołał zaszyfrować kopii zapasowej.") from exc

    def unprotect(self, data: bytes) -> bytes:
        if os.name != "nt":
            raise BackupError("Szyfrowane kopie zapasowe są obecnie dostępne tylko w Windows.")
        try:
            import win32crypt
            return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]
        except Exception as exc:
            raise BackupError(
                "Nie można odszyfrować kopii. Musi ją otwierać to samo konto Windows, które ją utworzyło."
            ) from exc


def default_backup_directory() -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))
    return root / "Asystent Poczty" / "Kopie zapasowe"


class BackupManager:
    def __init__(
        self,
        settings: QSettings,
        database_path: str | Path,
        drafts_directory: str | Path,
        restore_directory: str | Path | None = None,
        protector: Any | None = None,
    ) -> None:
        self.settings = settings
        self.database_path = Path(database_path)
        self.drafts_directory = Path(drafts_directory)
        self.restore_directory = Path(restore_directory) if restore_directory else self.database_path.parent / "restored_documents"
        self.protector = protector or DpapiProtector()
        self.restore_marker = self.database_path.parent / ".mailassistant-restore-in-progress.json"

    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _json_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [BackupManager._json_value(item) for item in value]
        return str(value)

    def _settings_snapshot(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key in self.settings.allKeys():
            if SENSITIVE_KEY.search(str(key)):
                continue
            values[str(key)] = self._json_value(self.settings.value(key))
        return values

    def _database_snapshot(self) -> bytes:
        if not self.database_path.is_file():
            raise BackupError("Nie znaleziono lokalnej bazy danych aplikacji.")
        source = sqlite3.connect(str(self.database_path), timeout=10)
        descriptor, temporary = tempfile.mkstemp(
            prefix="mailassistant_backup_", suffix=".sqlite3", dir=str(self.database_path.parent)
        )
        os.close(descriptor)
        destination = sqlite3.connect(temporary)
        try:
            source.backup(destination)
            if str(destination.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
                raise BackupError("Lokalna baza danych jest niespójna. Kopia nie została utworzona.")
            destination.commit()
        finally:
            destination.close()
            source.close()
        try:
            return Path(temporary).read_bytes()
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    @contextmanager
    def _temporary_database(data: bytes):
        descriptor, name = tempfile.mkstemp(prefix="mailassistant_restore_", suffix=".sqlite3")
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            yield path
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)

    @staticmethod
    def _validate_database(data: bytes) -> None:
        with BackupManager._temporary_database(data) as path:
            db = sqlite3.connect(str(path))
            try:
                if str(db.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
                    raise BackupError("Baza danych w kopii jest uszkodzona.")
            except sqlite3.DatabaseError as exc:
                raise BackupError("Baza danych w kopii jest uszkodzona.") from exc
            finally:
                db.close()

    @staticmethod
    def _documents_from_database(data: bytes) -> list[dict[str, str]]:
        with BackupManager._temporary_database(data) as path:
            db = sqlite3.connect(str(path))
            try:
                rows = db.execute(
                    "SELECT id,name,local_path FROM documents WHERE local_path<>'' ORDER BY id"
                ).fetchall()
                return [{"id": str(row[0]), "name": str(row[1]), "path": str(row[2])} for row in rows]
            finally:
                db.close()

    def _draft_payloads(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        if not self.drafts_directory.is_dir():
            return result
        for path in self.drafts_directory.glob("*.draft"):
            if not DRAFT_NAME.fullmatch(path.name):
                continue
            raw = path.read_bytes()
            if not raw.startswith(b"DPAPI1"):
                raise BackupError(f"Wersja robocza {path.name} ma nieobsługiwany format.")
            clear = self.protector.unprotect(raw[6:])
            try:
                value = json.loads(clear.decode("utf-8"))
            except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise BackupError(f"Wersja robocza {path.name} jest uszkodzona.") from exc
            if not isinstance(value, dict):
                raise BackupError(f"Wersja robocza {path.name} jest uszkodzona.")
            result[f"drafts/{path.name}.json"] = json.dumps(value, ensure_ascii=False).encode("utf-8")
        return result

    def create_backup(self, target: str | Path) -> dict[str, Any]:
        target_path = Path(target)
        if target_path.suffix.lower() != ".mabackup":
            target_path = target_path.with_suffix(".mabackup")
        database = self._database_snapshot()
        entries: dict[str, bytes] = {
            "database.sqlite3": database,
            "settings.json": json.dumps(self._settings_snapshot(), ensure_ascii=False, sort_keys=True).encode("utf-8"),
        }
        entries.update(self._draft_payloads())
        document_map: list[dict[str, str]] = []
        total_source = sum(len(value) for value in entries.values())
        for document in self._documents_from_database(database):
            source = Path(document["path"])
            if not source.is_file():
                continue
            size = source.stat().st_size
            total_source += size
            if total_source > MAX_BACKUP_SOURCE_BYTES:
                raise BackupError("Dane lokalne przekraczają limit kopii 250 MB.")
            archive_name = f"documents/{hashlib.sha256(document['id'].encode()).hexdigest()}.pdf"
            entries[archive_name] = source.read_bytes()
            document_map.append({
                "id": document["id"], "name": Path(document["name"]).name,
                "original_path": document["path"], "archive_name": archive_name,
            })
        manifest = {
            "format": "MailAssistantBackup", "version": BACKUP_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "encryption": "Windows DPAPI — bieżące konto użytkownika",
            "entries": {name: self._hash(data) for name, data in entries.items()},
            "documents": document_map,
            "draft_count": sum(1 for name in entries if name.startswith("drafts/")),
            "settings_count": len(json.loads(entries["settings.json"].decode("utf-8"))),
        }
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        encrypted = BACKUP_MAGIC + self.protector.protect(stream.getvalue())
        ensure_free_space(target_path.parent, len(encrypted) * 2)
        atomic_write_bytes(target_path, encrypted)
        summary = self.inspect_backup(target_path)
        summary["path"] = str(target_path)
        return summary

    def _read_backup(self, source: str | Path) -> tuple[dict[str, Any], dict[str, bytes]]:
        path = Path(source)
        if not path.is_file():
            raise BackupError("Nie znaleziono wybranej kopii zapasowej.")
        if path.stat().st_size > MAX_BACKUP_SOURCE_BYTES:
            raise BackupError("Plik kopii jest zbyt duży.")
        raw = path.read_bytes()
        if not raw.startswith(BACKUP_MAGIC):
            raise BackupError("Wybrany plik nie jest kopią zapasową Asystenta Poczty.")
        clear = self.protector.unprotect(raw[len(BACKUP_MAGIC):])
        try:
            with zipfile.ZipFile(io.BytesIO(clear), "r") as archive:
                names = archive.namelist()
                if any(name.startswith(("/", "\\")) or ".." in Path(name).parts for name in names):
                    raise BackupError("Kopia zawiera niedozwolone ścieżki.")
                if len(names) > 10000 or sum(item.file_size for item in archive.infolist()) > MAX_BACKUP_SOURCE_BYTES:
                    raise BackupError("Kopia po rozpakowaniu przekracza bezpieczny limit 250 MB.")
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                entries = {name: archive.read(name) for name in manifest.get("entries", {})}
        except (KeyError, ValueError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise BackupError("Kopia zapasowa jest uszkodzona albo niekompletna.") from exc
        if manifest.get("format") != "MailAssistantBackup" or int(manifest.get("version", 0)) != BACKUP_VERSION:
            raise BackupError("Ta wersja kopii nie jest obsługiwana przez aplikację.")
        for name, expected in manifest.get("entries", {}).items():
            if name not in entries or self._hash(entries[name]) != expected:
                raise BackupError(f"Plik {name} w kopii nie przeszedł kontroli integralności.")
        if "database.sqlite3" not in entries or "settings.json" not in entries:
            raise BackupError("Kopia nie zawiera wymaganych danych aplikacji.")
        self._validate_database(entries["database.sqlite3"])
        return manifest, entries

    def inspect_backup(self, source: str | Path) -> dict[str, Any]:
        manifest, entries = self._read_backup(source)
        return {
            "created_at": str(manifest.get("created_at") or ""),
            "documents": len(manifest.get("documents") or []),
            "drafts": int(manifest.get("draft_count") or 0),
            "settings": int(manifest.get("settings_count") or 0),
            "entries": len(entries),
        }

    @staticmethod
    def _safe_document_name(value: str, identifier: str) -> str:
        name = Path(str(value or "Dokument.pdf")).name
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        prefix = hashlib.sha256(identifier.encode()).hexdigest()[:10]
        return f"{prefix}-{name}"

    def _database_with_restored_paths(
        self, database: bytes, manifest: dict[str, Any], entries: dict[str, bytes], restore_root: Path,
    ) -> tuple[bytes, dict[str, str]]:
        path_map: dict[str, str] = {}
        with self._temporary_database(database) as database_file:
            db = sqlite3.connect(str(database_file))
            try:
                for document in manifest.get("documents") or []:
                    if not isinstance(document, dict):
                        continue
                    identifier = str(document.get("id") or "")
                    archive_name = str(document.get("archive_name") or "")
                    if not identifier or archive_name not in entries:
                        raise BackupError("Kopia dokumentów jest niekompletna.")
                    target = restore_root / self._safe_document_name(str(document.get("name") or ""), identifier)
                    atomic_write_bytes(target, entries[archive_name])
                    old_path = str(document.get("original_path") or "")
                    path_map[old_path] = str(target)
                    db.execute(
                        "UPDATE documents SET local_path=?,size=? WHERE id=?",
                        (str(target), len(entries[archive_name]), identifier),
                    )
                db.commit()
                if str(db.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
                    raise BackupError("Nie udało się przygotować spójnej bazy do przywrócenia.")
            finally:
                db.close()
            return database_file.read_bytes(), path_map

    @staticmethod
    def _replace_paths(value: Any, mapping: dict[str, str]) -> Any:
        if isinstance(value, str):
            for old, new in mapping.items():
                if old:
                    value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [BackupManager._replace_paths(item, mapping) for item in value]
        if isinstance(value, dict):
            return {key: BackupManager._replace_paths(item, mapping) for key, item in value.items()}
        return value

    def _apply_backup(self, manifest: dict[str, Any], entries: dict[str, bytes]) -> None:
        restore_root = self.restore_directory / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        restore_root.mkdir(parents=True, exist_ok=False)
        try:
            os.chmod(restore_root, 0o700)
        except OSError:
            pass
        database, path_map = self._database_with_restored_paths(
            entries["database.sqlite3"], manifest, entries, restore_root
        )
        if self.database_path.is_file():
            existing = sqlite3.connect(str(self.database_path), timeout=10)
            try:
                existing.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                existing.close()
        for suffix in ("-wal", "-shm"):
            Path(str(self.database_path) + suffix).unlink(missing_ok=True)
        atomic_write_bytes(self.database_path, database)
        self.drafts_directory.mkdir(parents=True, exist_ok=True)
        for current in self.drafts_directory.glob("*.draft"):
            if DRAFT_NAME.fullmatch(current.name):
                current.unlink()
        for name, data in entries.items():
            if not name.startswith("drafts/") or not name.endswith(".draft.json"):
                continue
            draft_name = Path(name).name[:-5]
            if not DRAFT_NAME.fullmatch(draft_name):
                raise BackupError("Kopia zawiera nieprawidłową nazwę wersji roboczej.")
            try:
                draft = json.loads(data.decode("utf-8"))
                draft = self._replace_paths(draft, path_map)
                clear = json.dumps(draft, ensure_ascii=False).encode("utf-8")
            except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise BackupError("Wersja robocza w kopii jest uszkodzona.") from exc
            atomic_write_bytes(self.drafts_directory / draft_name, b"DPAPI1" + self.protector.protect(clear))
        settings = json.loads(entries["settings.json"].decode("utf-8"))
        if not isinstance(settings, dict):
            raise BackupError("Ustawienia w kopii są uszkodzone.")
        settings = self._replace_paths(settings, path_map)
        self.settings.clear()
        for key, value in settings.items():
            if not SENSITIVE_KEY.search(str(key)):
                self.settings.setValue(str(key), value)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise BackupError("Nie udało się zapisać przywróconych ustawień.")

    def restore_backup(self, source: str | Path, safety_directory: str | Path) -> dict[str, Any]:
        manifest, entries = self._read_backup(source)
        safety_root = Path(safety_directory)
        safety_root.mkdir(parents=True, exist_ok=True)
        safety_path = safety_root / f"bezpieczenstwo-przed-przywroceniem-{datetime.now():%Y%m%d-%H%M%S-%f}.mabackup"
        self.create_backup(safety_path)
        marker = json.dumps(
            {"safety_backup": str(safety_path), "created_at": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False,
        ).encode("utf-8")
        atomic_write_bytes(self.restore_marker, marker)
        try:
            self._apply_backup(manifest, entries)
            self._validate_database(self._database_snapshot())
        except Exception as exc:
            try:
                rollback_manifest, rollback_entries = self._read_backup(safety_path)
                self._apply_backup(rollback_manifest, rollback_entries)
                self._validate_database(self._database_snapshot())
                self.restore_marker.unlink(missing_ok=True)
            except Exception as rollback_exc:
                raise BackupError(
                    "Przywracanie nie powiodło się, a automatyczny powrót także zawiódł. "
                    "Nie usuwaj kopii bezpieczeństwa i uruchom aplikację ponownie."
                ) from exc
            raise BackupError("Przywracanie nie powiodło się. Poprzedni stan został automatycznie odtworzony.") from exc
        self.restore_marker.unlink(missing_ok=True)
        return {"safety_backup": str(safety_path), **self.inspect_backup(source)}

    def recover_interrupted_restore(self) -> dict[str, Any] | None:
        """Kończy awaryjny powrót po przerwaniu procesu przywracania."""
        if not self.restore_marker.is_file():
            return None
        try:
            if self.restore_marker.stat().st_size > 65536:
                raise BackupError("Znacznik przerwanego przywracania jest nieprawidłowy.")
            marker = json.loads(self.restore_marker.read_text(encoding="utf-8"))
            safety_path = Path(str(marker.get("safety_backup") or ""))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError, AttributeError) as exc:
            raise BackupError("Nie można odczytać informacji o przerwanym przywracaniu.") from exc
        manifest, entries = self._read_backup(safety_path)
        self._apply_backup(manifest, entries)
        self._validate_database(self._database_snapshot())
        self.restore_marker.unlink(missing_ok=True)
        return {"safety_backup": str(safety_path), **self.inspect_backup(safety_path)}

    @staticmethod
    def prune_backups(directory: str | Path, keep: int) -> int:
        root = Path(directory)
        files = sorted(root.glob("asystent-poczty-*.mabackup"), key=lambda path: path.stat().st_mtime, reverse=True)
        removed = 0
        for path in files[max(1, int(keep)):]:
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                # Nowa, zweryfikowana kopia pozostaje poprawna nawet wtedy,
                # gdy Windows chwilowo blokuje jeden ze starszych plików.
                continue
        return removed
