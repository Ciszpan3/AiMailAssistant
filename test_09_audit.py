import os
import tempfile
import time
import unittest
from email.message import EmailMessage
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QPushButton

from mailassistant_qt.devices import images_to_pdf, validate_pdf
from mailassistant_qt.documents import DocumentIndex
from mailassistant_qt.logic import MailClient
from mailassistant_qt.main_window import MessagePage, SettingsPage
from mailassistant_qt.documents_ui import DocumentsPage
from mailassistant_qt.reply_ui import ReplyPage
from mailassistant_qt.state import AppSettings


class LargeMailboxIMAP:
    def __init__(self, count: int) -> None:
        self.ids = [str(value).encode("ascii") for value in range(1, count + 1)]
        self.fetch_count = 0

    def select(self, _folder, readonly=True):
        if not readonly:
            raise AssertionError("Skrzynka testowa może być otwarta wyłącznie do odczytu")
        return "OK", [str(len(self.ids)).encode("ascii")]

    def response(self, _name):
        return "OK", [b"9001"]

    def uid(self, operation, *args):
        if operation == "search":
            return "OK", [b" ".join(self.ids)]
        if operation != "fetch":
            raise AssertionError(operation)
        self.fetch_count += 1
        uid = args[0].decode("ascii") if isinstance(args[0], bytes) else str(args[0])
        message = EmailMessage()
        message["From"] = f"Nadawca {uid} <n{uid}@example.test>"
        message["To"] = "konto@example.test"
        message["Subject"] = f"Wiadomość {uid}"
        message["Date"] = "Tue, 27 Oct 2026 10:30:00 +0100"
        message["Message-ID"] = f"<{uid}@example.test>"
        header = message.as_bytes()
        return "OK", [(b"1 (FLAGS () BODYSTRUCTURE NIL)", header), (b"BODY[TEXT]", b"Fragment"), b")"]


class Test09AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mailbox_100_1000_10000_is_paginated_and_bounded(self):
        for count in (100, 1000, 10000):
            connection = LargeMailboxIMAP(count)
            client = MailClient()
            client.connection = connection
            started = time.perf_counter()
            result = client.load_folder("INBOX", 0, 100)
            elapsed = time.perf_counter() - started
            self.assertEqual(result["total"], count)
            self.assertEqual(len(result["rows"]), min(count, 100))
            self.assertEqual(connection.fetch_count, min(count, 100))
            self.assertLess(elapsed, 10.0)

    def test_ten_thousand_documents_rebuild_and_search_remain_bounded(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            index = DocumentIndex(Path(directory) / "documents.sqlite3")
            records = [
                {
                    "source_key": f"source-{number}",
                    "name": f"dokument-{number:05d}.pdf",
                    "category": "received",
                    "source_kind": "performance-test",
                }
                for number in range(10000)
            ]
            started = time.perf_counter()
            self.assertEqual(index.rebuild("konto@wp.pl", records), 10000)
            rebuilt_in = time.perf_counter() - started
            started = time.perf_counter()
            result = index.search("konto@wp.pl", "dokument-09999")
            searched_in = time.perf_counter() - started
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["name"], "dokument-09999.pdf")
            self.assertLess(rebuilt_in, 15.0)
            self.assertLess(searched_in, 5.0)

    def test_twenty_page_scan_is_one_valid_pdf_without_real_device(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            pages = []
            for number in range(20):
                path = Path(directory) / f"strona-{number:02d}.png"
                image = QImage(220, 320, QImage.Format.Format_RGB32)
                image.fill(QColor("white" if number % 2 == 0 else "lightgray"))
                self.assertTrue(image.save(str(path)))
                pages.append(str(path))
            output = Path(directory) / "wielostronicowy-skan.pdf"
            images_to_pdf(pages, str(output))
            _path, page_count = validate_pdf(output)
            self.assertEqual(page_count, 20)

    def test_backup_controls_are_plain_polish_and_keyboard_reachable(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            page = SettingsPage(AppSettings(settings))
            self.assertEqual(page.backup_button.text(), "Utwórz kopię zapasową")
            self.assertEqual(page.restore_button.text(), "Przywróć dane z kopii")
            self.assertNotEqual(page.backup_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertNotEqual(page.restore_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertTrue(page.backup_status.wordWrap())
            page.deleteLater()

    def test_document_search_uses_clear_local_wording_without_ai_question_button(self):
        page = DocumentsPage()
        labels = [button.text() for button in page.findChildren(QPushButton)]
        self.assertIn("Szukaj w dokumentach", labels)
        self.assertNotIn("Zapytaj po polsku", labels)
        self.assertIn("nazwę pliku", page.query.placeholderText())
        self.assertFalse(hasattr(page, "pdf_view"))
        page.deleteLater()

    def test_long_action_buttons_show_progress_and_are_restored(self):
        reply = ReplyPage()
        reply.set_context({"to": "a@example.com", "subject": "Re: Test", "body": "Treść"})
        reply.set_busy(True, "Lokalna Ollama przygotowuje propozycję odpowiedzi…")
        self.assertFalse(reply.ai_button.isEnabled())
        self.assertEqual(reply.ai_button.text(), "Generowanie odpowiedzi…")
        reply.set_busy(False)
        self.assertTrue(reply.ai_button.isEnabled())
        self.assertEqual(reply.ai_button.text(), "Pomóż mi napisać odpowiedź")

        message = MessagePage()
        message.show_message("1", {"subject": "Test"}, "Treść wiadomości", [])
        message.set_analysis_busy(True)
        self.assertEqual(message.analyze_button.text(), "Wyjaśniam wiadomość…")
        self.assertFalse(message.analyze_button.isEnabled())
        message.set_analysis_busy(False)
        self.assertEqual(message.analyze_button.text(), "Wyjaśnij wiadomość")
        self.assertTrue(message.analyze_button.isEnabled())

        documents = DocumentsPage()
        documents.set_rows([{"id": "1", "name": "test.pdf", "local_path": "", "category": "received"}])
        documents.set_document_busy(True, "Pobieranie dokumentu z serwera poczty…")
        self.assertEqual(documents.open_button.text(), "Pobieranie PDF…")
        self.assertFalse(documents.open_button.isEnabled())
        documents.set_document_busy(False)
        self.assertEqual(documents.open_button.text(), "Otwórz podgląd PDF")
        self.assertTrue(documents.open_button.isEnabled())
        reply.deleteLater(); message.deleteLater(); documents.deleteLater()


if __name__ == "__main__":
    unittest.main()
