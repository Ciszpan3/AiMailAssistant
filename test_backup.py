import json
import io
import os
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings

from mailassistant_qt.backup import BACKUP_MAGIC, BackupError, BackupManager
from mailassistant_qt.documents import DocumentIndex
from mailassistant_qt.reply import DraftStore
from mailassistant_qt.storage import StorageError


class FakeProtector:
    def protect(self, data: bytes) -> bytes:
        return b"TEST1" + data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"TEST1"):
            raise BackupError("wrong key")
        return data[5:][::-1]


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.getcwd())
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("account/address", "osoba@wp.pl")
        self.settings.setValue("progress/example", "[\"task-1\"]")
        self.settings.setValue("account/password", "NIE_WOLNO_KOPIOWAC")
        self.settings.sync()
        self.index = DocumentIndex(self.root / "documents.sqlite3")
        self.pdf = self.root / "scan.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\nbackup-test")
        self.document_id = self.index.add_local_document("osoba@wp.pl", str(self.pdf), "scanned", "mail-1")
        self.drafts = self.root / "drafts"
        self.drafts.mkdir()
        self.draft_name = "a" * 64 + ".draft"
        self.protector = FakeProtector()
        draft = json.dumps({"body": "Wersja robocza", "attachments": [{"path": str(self.pdf)}]}).encode()
        (self.drafts / self.draft_name).write_bytes(b"DPAPI1" + self.protector.protect(draft))
        self.manager = BackupManager(
            self.settings, self.index.path, self.drafts, self.root / "restored", self.protector
        )

    def rewrite_manifest(self, target: Path, change) -> None:
        raw = target.read_bytes()
        clear = self.protector.unprotect(raw[len(BACKUP_MAGIC):])
        source = zipfile.ZipFile(io.BytesIO(clear), "r")
        values = {name: source.read(name) for name in source.namelist()}
        source.close()
        manifest = json.loads(values["manifest.json"].decode("utf-8"))
        change(manifest)
        values["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in values.items():
                archive.writestr(name, value)
        target.write_bytes(BACKUP_MAGIC + self.protector.protect(output.getvalue()))

    def tearDown(self):
        self.settings.sync()
        del self.settings
        self.temp.cleanup()

    def test_create_inspect_and_restore_on_clean_state(self):
        target = self.root / "backup.mabackup"
        created = self.manager.create_backup(target)
        self.assertTrue(target.read_bytes().startswith(BACKUP_MAGIC))
        self.assertEqual(created["documents"], 1)
        self.assertEqual(created["drafts"], 1)

        self.settings.clear()
        self.settings.setValue("temporary", "changed")
        self.settings.sync()
        db = sqlite3.connect(str(self.index.path))
        try:
            db.execute("DELETE FROM documents")
            db.commit()
        finally:
            db.close()
        (self.drafts / self.draft_name).unlink()
        self.pdf.unlink()

        restored = self.manager.restore_backup(target, self.root / "safety")
        self.assertTrue(Path(restored["safety_backup"]).is_file())
        self.assertEqual(self.settings.value("account/address"), "osoba@wp.pl")
        self.assertIsNone(self.settings.value("account/password"))
        db = sqlite3.connect(str(self.index.path))
        try:
            row = db.execute("SELECT local_path FROM documents WHERE id=?", (self.document_id,)).fetchone()
        finally:
            db.close()
        self.assertIsNotNone(row)
        self.assertTrue(Path(row[0]).is_file())
        raw_draft = (self.drafts / self.draft_name).read_bytes()
        draft = json.loads(self.protector.unprotect(raw_draft[6:]).decode())
        self.assertTrue(Path(draft["attachments"][0]["path"]).is_file())

    def test_corrupted_backup_is_rejected(self):
        target = self.root / "backup.mabackup"
        self.manager.create_backup(target)
        raw = bytearray(target.read_bytes())
        raw[-10:] = b"corruption"
        target.write_bytes(raw)
        with self.assertRaises(BackupError):
            self.manager.inspect_backup(target)

    def test_no_space_does_not_replace_previous_good_backup(self):
        target = self.root / "backup.mabackup"
        target.write_bytes(b"previous-good-backup")
        with patch("mailassistant_qt.backup.ensure_free_space", side_effect=StorageError("Brakuje miejsca")):
            with self.assertRaisesRegex(StorageError, "Brakuje miejsca"):
                self.manager.create_backup(target)
        self.assertEqual(target.read_bytes(), b"previous-good-backup")

    def test_retention_never_removes_newest_backup(self):
        directory = self.root / "retention"
        directory.mkdir()
        files = []
        for index in range(4):
            path = directory / f"asystent-poczty-2026102{index}.mabackup"
            path.write_bytes(str(index).encode())
            os.utime(path, (index + 1, index + 1))
            files.append(path)
        removed = BackupManager.prune_backups(directory, 2)
        self.assertEqual(removed, 2)
        self.assertTrue(files[-1].is_file())

    def test_incompatible_backup_version_is_rejected(self):
        target = self.root / "future.mabackup"
        self.manager.create_backup(target)
        self.rewrite_manifest(target, lambda manifest: manifest.update(version=999))
        with self.assertRaisesRegex(BackupError, "wersja"):
            self.manager.inspect_backup(target)

    def test_failed_restore_rolls_back_to_state_from_safety_backup(self):
        source = self.root / "source.mabackup"
        self.manager.create_backup(source)
        self.settings.setValue("temporary", "stan-przed-przywracaniem")
        self.settings.sync()
        original_apply = self.manager._apply_backup
        calls = 0

        def interrupted_apply(manifest, entries):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.settings.clear()
                self.settings.sync()
                db = sqlite3.connect(str(self.index.path))
                try:
                    db.execute("DELETE FROM documents")
                    db.commit()
                finally:
                    db.close()
                raise OSError("symulowane przerwanie")
            return original_apply(manifest, entries)

        with patch.object(self.manager, "_apply_backup", side_effect=interrupted_apply):
            with self.assertRaisesRegex(BackupError, "Poprzedni stan"):
                self.manager.restore_backup(source, self.root / "safety")
        self.assertEqual(self.settings.value("temporary"), "stan-przed-przywracaniem")
        self.assertFalse(self.manager.restore_marker.exists())
        db = sqlite3.connect(str(self.index.path))
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
        finally:
            db.close()

    def test_startup_recovers_state_after_interrupted_restore(self):
        safety = self.root / "safety-interrupted.mabackup"
        self.manager.create_backup(safety)
        self.settings.clear()
        self.settings.setValue("temporary", "uszkodzony-stan")
        self.settings.sync()
        db = sqlite3.connect(str(self.index.path))
        try:
            db.execute("DELETE FROM documents")
            db.commit()
        finally:
            db.close()
        self.manager.restore_marker.write_text(
            json.dumps({"safety_backup": str(safety), "created_at": datetime.now().isoformat()}),
            encoding="utf-8",
        )
        recovered = self.manager.recover_interrupted_restore()
        self.assertEqual(recovered["safety_backup"], str(safety))
        self.assertFalse(self.manager.restore_marker.exists())
        self.assertEqual(self.settings.value("account/address"), "osoba@wp.pl")
        db = sqlite3.connect(str(self.index.path))
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
        finally:
            db.close()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI jest dostępne tylko w Windows")
    def test_real_windows_dpapi_backup_is_actually_restorable(self):
        real_drafts = self.root / "real-drafts"
        store = DraftStore(real_drafts)
        store.save("osoba@wp.pl", "mail-real", {"body": "Poufny szkic", "attachments": []})
        manager = BackupManager(
            self.settings, self.index.path, real_drafts, self.root / "real-restored"
        )
        target = self.root / "real-dpapi.mabackup"
        created = manager.create_backup(target)
        self.assertEqual(created["drafts"], 1)
        self.assertNotIn(b"Poufny szkic", target.read_bytes())

        self.settings.clear()
        self.settings.setValue("temporary", "zmienione")
        self.settings.sync()
        db = sqlite3.connect(str(self.index.path))
        try:
            db.execute("DELETE FROM documents")
            db.commit()
        finally:
            db.close()
        manager.restore_backup(target, self.root / "real-safety")
        self.assertEqual(self.settings.value("account/address"), "osoba@wp.pl")
        self.assertEqual(store.load("osoba@wp.pl", "mail-real")["body"], "Poufny szkic")


if __name__ == "__main__":
    unittest.main()
