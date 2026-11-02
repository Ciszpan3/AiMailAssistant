"""Ustawienia i postęp użytkownika zapisane przez QSettings."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Iterable

from PySide6.QtCore import QSettings

from .backup import default_backup_directory


class AppSettings:
    ORGANIZATION = "MailAssistant"
    APPLICATION = "AsystentPocztyQt"

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings(self.ORGANIZATION, self.APPLICATION)

    def _sync(self) -> None:
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("Nie udało się zapisać ustawień i postępu na dysku.")

    @property
    def address(self) -> str:
        return str(self.settings.value("account/address", ""))

    @address.setter
    def address(self, value: str) -> None:
        self.settings.setValue("account/address", value.strip())
        self._sync()

    @property
    def font_scale(self) -> int:
        try:
            return max(90, min(140, int(self.settings.value("appearance/font_scale", 120))))
        except (TypeError, ValueError):
            return 120

    @font_scale.setter
    def font_scale(self, value: int) -> None:
        self.settings.setValue("appearance/font_scale", max(90, min(140, int(value))))
        self._sync()

    @property
    def high_contrast(self) -> bool:
        raw = self.settings.value("appearance/high_contrast", False)
        return raw is True or str(raw).lower() in {"1", "true", "yes"}

    @high_contrast.setter
    def high_contrast(self, value: bool) -> None:
        self.settings.setValue("appearance/high_contrast", bool(value))
        self._sync()

    @property
    def simple_mode(self) -> bool:
        raw = self.settings.value("appearance/simple_mode", True)
        return raw is True or str(raw).lower() in {"1", "true", "yes"}

    @simple_mode.setter
    def simple_mode(self, value: bool) -> None:
        self.settings.setValue("appearance/simple_mode", bool(value))
        self._sync()

    @property
    def sidebar_collapsed(self) -> bool:
        raw = self.settings.value("appearance/sidebar_collapsed", False)
        return raw is True or str(raw).lower() in {"1", "true", "yes"}

    @sidebar_collapsed.setter
    def sidebar_collapsed(self, value: bool) -> None:
        self.settings.setValue("appearance/sidebar_collapsed", bool(value))
        self._sync()

    @property
    def first_run_complete(self) -> bool:
        raw = self.settings.value("setup/first_run_complete", False)
        return raw is True or str(raw).lower() in {"1", "true", "yes"}

    @first_run_complete.setter
    def first_run_complete(self, value: bool) -> None:
        self.settings.setValue("setup/first_run_complete", bool(value))
        self._sync()

    @staticmethod
    def _progress_key(address: str, message_identity: str) -> str:
        identity = f"{address.strip().lower()}|{message_identity}".encode("utf-8")
        return "progress/" + hashlib.sha256(identity).hexdigest()

    def completed_tasks(self, address: str, message_identity: str) -> set[str]:
        raw = self.settings.value(self._progress_key(address, message_identity), "[]")
        try:
            values = json.loads(str(raw))
            return {str(item) for item in values if str(item)} if isinstance(values, list) else set()
        except (TypeError, ValueError, json.JSONDecodeError):
            return set()

    def save_completed_tasks(self, address: str, message_identity: str, task_ids: Iterable[str]) -> None:
        values = sorted({str(item) for item in task_ids if str(item)})
        self.settings.setValue(self._progress_key(address, message_identity), json.dumps(values, ensure_ascii=False))
        self._sync()

    def dashboard_plan(self, address: str) -> dict[str, object]:
        raw = self.settings.value(self._message_key("dashboard", address, "last-plan"), "{}")
        try:
            value = json.loads(str(raw))
            return dict(value) if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def save_dashboard_plan(self, address: str, value: dict[str, object]) -> None:
        self.settings.setValue(
            self._message_key("dashboard", address, "last-plan"),
            json.dumps(value, ensure_ascii=False),
        )
        self._sync()

    @staticmethod
    def _message_key(section: str, address: str, message_identity: str) -> str:
        identity = f"{address.strip().lower()}|{message_identity}".encode("utf-8")
        return f"{section}/" + hashlib.sha256(identity).hexdigest()

    def scanned_documents(self, address: str, message_identity: str) -> list[dict[str, object]]:
        raw = self.settings.value(self._message_key("scans", address, message_identity), "[]")
        try:
            values = json.loads(str(raw))
            if not isinstance(values, list):
                return []
            return [dict(item) for item in values if isinstance(item, dict) and item.get("path")]
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    def save_scanned_document(
        self, address: str, message_identity: str, path: str, approved: bool = True
    ) -> None:
        values = self.scanned_documents(address, message_identity)
        normalized = str(path)
        values = [item for item in values if str(item.get("path", "")).lower() != normalized.lower()]
        values.append({"path": normalized, "approved": bool(approved)})
        self.settings.setValue(
            self._message_key("scans", address, message_identity),
            json.dumps(values, ensure_ascii=False),
        )
        self._sync()

    def send_status(self, address: str, message_identity: str) -> dict[str, str]:
        raw = self.settings.value(self._message_key("send_status", address, message_identity), "{}")
        try:
            value = json.loads(str(raw))
            return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def save_send_status(
        self, address: str, message_identity: str, status: str, message_id: str = ""
    ) -> None:
        self.settings.setValue(
            self._message_key("send_status", address, message_identity),
            json.dumps({"status": status, "message_id": message_id}, ensure_ascii=False),
        )
        self._sync()

    @property
    def default_printer(self) -> str:
        return str(self.settings.value("devices/default_printer", ""))

    @default_printer.setter
    def default_printer(self, value: str) -> None:
        self.settings.setValue("devices/default_printer", value)
        self._sync()

    @property
    def default_scanner(self) -> str:
        return str(self.settings.value("devices/default_scanner", ""))

    @default_scanner.setter
    def default_scanner(self, value: str) -> None:
        self.settings.setValue("devices/default_scanner", value)
        self._sync()

    @property
    def device_links(self) -> dict[str, str]:
        raw = self.settings.value("devices/manual_links", "{}")
        try:
            value = json.loads(str(raw))
            return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    @device_links.setter
    def device_links(self, value: dict[str, str]) -> None:
        self.settings.setValue("devices/manual_links", json.dumps(value, ensure_ascii=False, sort_keys=True))
        self._sync()

    @property
    def backup_directory(self) -> str:
        return str(self.settings.value("backup/directory", str(default_backup_directory())))

    @backup_directory.setter
    def backup_directory(self, value: str) -> None:
        self.settings.setValue("backup/directory", str(value).strip())
        self._sync()

    @property
    def automatic_backup(self) -> bool:
        raw = self.settings.value("backup/automatic", True)
        return raw is True or str(raw).lower() in {"1", "true", "yes"}

    @automatic_backup.setter
    def automatic_backup(self, value: bool) -> None:
        self.settings.setValue("backup/automatic", bool(value))
        self._sync()

    @property
    def backup_keep(self) -> int:
        try:
            return max(1, min(30, int(self.settings.value("backup/keep", 7))))
        except (TypeError, ValueError):
            return 7

    @backup_keep.setter
    def backup_keep(self, value: int) -> None:
        self.settings.setValue("backup/keep", max(1, min(30, int(value))))
        self._sync()

    @property
    def last_backup_date(self) -> str:
        return str(self.settings.value("backup/last_successful_date", ""))

    @last_backup_date.setter
    def last_backup_date(self, value: str) -> None:
        self.settings.setValue("backup/last_successful_date", str(value))
        self._sync()


def stable_task_key(task: dict, index: int = 0) -> str:
    """Stabilny klucz checkboxa niezależny od identyfikatora wymyślonego przez model."""
    source = str(task.get("source_quote") or "").strip()
    parts = (
        source or str(task.get("action") or ""),
        str(task.get("deadline") or ""),
        str(task.get("quantity") or ""),
        str(index),
    )
    normalized = "|".join(parts).lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized.replace("|", ""):
        normalized = f"krok-{index}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

