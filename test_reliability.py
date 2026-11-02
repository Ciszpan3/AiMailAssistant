import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from mailassistant_qt.devices import images_to_pdf
from mailassistant_qt.logic import MailClient
from mailassistant_qt.state import AppSettings
from mailassistant_qt.storage import StorageError, atomic_write_bytes


class ReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_atomic_write_replaces_complete_file(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            target = Path(directory) / "document.pdf"
            target.write_bytes(b"old")
            atomic_write_bytes(target, b"%PDF-1.4\nnew")
            self.assertEqual(target.read_bytes(), b"%PDF-1.4\nnew")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_failed_atomic_replace_keeps_previous_file(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            target = Path(directory) / "document.pdf"
            target.write_bytes(b"old-valid-file")
            with patch("mailassistant_qt.storage.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    atomic_write_bytes(target, b"new-incomplete-file")
            self.assertEqual(target.read_bytes(), b"old-valid-file")

    def test_attachment_must_be_a_bounded_pdf(self):
        part = MagicMock()
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            target = Path(directory) / "attachment.pdf"
            part.get_payload.return_value = b"not a pdf"
            with self.assertRaisesRegex(StorageError, "prawidłowym dokumentem PDF"):
                MailClient.save_attachment({"part": part}, target)
            with patch("mailassistant_qt.logic.MAX_DOCUMENT_BYTES", 4):
                part.get_payload.return_value = b"%PDF-1.4"
                with self.assertRaisesRegex(StorageError, "100 MB"):
                    MailClient.save_attachment({"part": part}, target)

    def test_failed_scan_does_not_overwrite_existing_pdf(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            target = Path(directory) / "scan.pdf"
            target.write_bytes(b"%PDF-1.4\nold")
            with self.assertRaisesRegex(Exception, "Nie można odczytać"):
                images_to_pdf([str(Path(directory) / "missing.png")], str(target))
            self.assertEqual(target.read_bytes(), b"%PDF-1.4\nold")

    def test_settings_write_error_is_not_silently_ignored(self):
        settings = MagicMock()
        settings.status.return_value = QSettings.Status.AccessError
        state = AppSettings(settings)
        with self.assertRaisesRegex(OSError, "ustawień i postępu"):
            state.address = "osoba@wp.pl"


if __name__ == "__main__":
    unittest.main()
