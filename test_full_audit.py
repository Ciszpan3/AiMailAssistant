"""Końcowe testy przepływu wiadomość -> szczegóły -> dokumenty PDF."""

import gc
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mailassistant_qt.logic import MailClient
from mailassistant_qt.main_window import MailPage, MainWindow
from mailassistant_qt.state import AppSettings
from mailassistant_qt.workers import MailService


class AuditClient:
    uid_validity = "10"

    def connect(self, _address, _password):
        return None

    def disconnect(self):
        return None

    def abort(self):
        return None

    def get_details(self, _uid):
        return "Pełna treść", [{"name": "formularz.pdf", "part": object()}]


class FullApplicationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        temporary = tempfile.TemporaryDirectory(dir=os.getcwd())
        settings = QSettings(str(Path(temporary.name) / "settings.ini"), QSettings.Format.IniFormat)
        state = AppSettings(settings)
        state.first_run_complete = True
        window = MainWindow(state=state, mail_service=MailService(AuditClient()))
        return temporary, window

    def close_window(self, temporary, window):
        window.mail_thread.quit()
        window.mail_thread.wait(2000)
        window.deleteLater()
        self.app.processEvents()
        gc.collect()
        temporary.cleanup()

    def test_01_real_click_opens_the_selected_message(self):
        page = MailPage()
        selected = []
        page.message_selected.connect(selected.append)
        page.set_messages([{
            "uid": "mail:1", "sender": "Anna", "subject": "Dokumenty",
            "date": "29.10.2026", "snippet": "W załączniku formularz.",
            "has_attachments": True, "attachment_names": ["formularz.pdf"],
        }])
        page.resize(900, 700)
        page.show()
        self.app.processEvents()
        item = page.message_list.item(0)
        QTest.mouseClick(
            page.message_list.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, page.message_list.visualItemRect(item).center(),
        )
        self.app.processEvents()
        self.assertEqual(selected, ["mail:1"])
        page.close()
        page.deleteLater()

    def test_02_cached_body_without_attachment_cache_forces_complete_fetch(self):
        temporary, window = self.make_window()
        try:
            window._mail_connected = True
            uid = "mail:cached"
            window.mail_page.rows[uid] = {
                "uid": uid, "sender": "Anna", "subject": "Formularz",
                "attachment_names": ["formularz.pdf"], "has_attachments": True,
            }
            window.mail_page.bodies_by_uid[uid] = "Treść pobrana wcześniej do wyszukiwarki."
            requested = []
            window.load_mail_details.connect(requested.append)
            window._open_message(uid)
            self.assertEqual(requested, [uid])
            self.assertEqual(window.message_page.attachment_names, ["formularz.pdf"])
        finally:
            self.close_window(temporary, window)

    def test_03_semantic_indexing_preserves_pdf_parts_and_names(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as cache_root:
            service = MailService(AuditClient(), cache_root=cache_root)
            received = []
            service.semantic_details_ready.connect(
                lambda uid, body, names: received.append((uid, body, names))
            )
            service.load_semantic_details("mail:1")
            self.assertEqual(received, [("mail:1", "Pełna treść", ["formularz.pdf"])])
            self.assertEqual(service._attachments["mail:1"][0]["name"], "formularz.pdf")
            service.disconnect()

    def test_04_inline_pdf_without_filename_is_not_lost(self):
        message = EmailMessage()
        message["Subject"] = "PDF osadzony"
        message.set_content("Treść wiadomości")
        unnamed_pdf = EmailMessage()
        unnamed_pdf.set_content(b"%PDF-1.4\n%%EOF", maintype="application", subtype="pdf")
        unnamed_pdf.add_header("Content-Disposition", "inline")
        message.make_mixed()
        message.attach(unnamed_pdf)
        client = MailClient()
        client.messages["mail:inline"] = message
        body, attachments = client.get_details("mail:inline")
        self.assertIn("Treść wiadomości", body)
        self.assertEqual([item["name"] for item in attachments], ["Załącznik PDF.pdf"])

    def test_05_pdf_content_type_with_incomplete_name_gets_safe_extension(self):
        message = EmailMessage()
        message.set_content("Treść")
        message.add_attachment(
            b"%PDF-1.4\n%%EOF", maintype="application", subtype="pdf", filename="formularz"
        )
        client = MailClient()
        client.messages["mail:name"] = message
        _body, attachments = client.get_details("mail:name")
        self.assertEqual([item["name"] for item in attachments], ["formularz.pdf"])

    def test_06_semantic_result_updates_message_attachment_cache(self):
        temporary, window = self.make_window()
        try:
            uid = "mail:semantic"
            window.account_address = "konto@example.com"
            window.mail_page.rows[uid] = {
                "uid": uid, "folder": "INBOX", "uid_validity": "1", "server_uid": "7",
                "subject": "Dokument", "sender": "Anna",
            }
            window._semantic_current_uid = uid
            with patch.object(window, "_start_semantic_embedding") as start:
                window._semantic_details_ready(uid, "Treść", ["dokument.pdf"])
            self.assertEqual(window.mail_page.bodies_by_uid[uid], "Treść")
            self.assertEqual(window.mail_page.attachments_by_uid[uid], ["dokument.pdf"])
            start.assert_called_once_with(uid, "Treść")
        finally:
            self.close_window(temporary, window)


if __name__ == "__main__":
    unittest.main()
