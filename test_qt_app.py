import os
import gc
import io
import json
import tempfile
import time
import unittest
import urllib.error
from copy import deepcopy
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

import ai_assistant_04a as reference
from mailassistant_qt import core
from mailassistant_qt.main_window import MailPage, MainWindow, TasksPage
from mailassistant_qt.devices import create_test_pdf
from mailassistant_qt.state import AppSettings, stable_task_key
from mailassistant_qt.styles import ensure_readable_font, stylesheet
from mailassistant_qt.workers import FunctionWorker, MailService, check_ollama
from test_04a1 import COMPLEX_BODY


SAMPLE_ANALYSIS = {
    "summary": "Przygotuj dokumenty i potwierdź udział w spotkaniu.",
    "events": [
        {
            "id": "event-1",
            "title": "Spotkanie organizacyjne",
            "start": "2026-11-03T10:30",
            "location": "sala konferencyjna nr 4",
            "condition": "",
            "confidence": "high",
            "source_quote": "Spotkanie organizacyjne odbędzie się 3 listopada 2026 r. o godzinie 10:30",
        }
    ],
    "tasks": [
        {
            "id": "task-1",
            "action": "Przygotuj dwa podpisane egzemplarze formularza",
            "deadline": "",
            "quantity": 2,
            "condition": "",
            "instruction": "",
            "depends_on": [],
            "confidence": "high",
            "source_quote": "wydrukować w dwóch egzemplarzach",
        },
        {
            "id": "task-2",
            "action": "Wyślij podpisany skan",
            "deadline": "2026-10-29T16:00",
            "quantity": 0,
            "condition": "Po sprawdzeniu aktualności adresu",
            "instruction": "Skan wszystkich stron w jednym pliku PDF",
            "depends_on": ["task-1"],
            "confidence": "high",
            "source_quote": "Podpisany skan proszę odesłać najpóźniej do 29 października 2026 r. do godziny 16:00",
        },
        {
            "id": "task-3",
            "action": "Potwierdź udział w spotkaniu",
            "deadline": "2026-10-30T12:00",
            "quantity": 0,
            "condition": "Jeśli nie możesz uczestniczyć, zaproponuj dwa inne terminy",
            "instruction": "",
            "depends_on": [],
            "confidence": "high",
            "source_quote": "Proszę potwierdzić udział do 30 października 2026 r. do godziny 12:00",
        },
        {
            "id": "task-4",
            "action": "Przeczytaj instrukcję przed spotkaniem",
            "deadline": "",
            "quantity": 0,
            "condition": "",
            "instruction": "Nie drukuj i nie odsyłaj instrukcji",
            "depends_on": [],
            "confidence": "high",
            "source_quote": "Proszę go przeczytać przed spotkaniem. Nie trzeba go drukować ani odsyłać.",
        },
    ],
    "warnings": [],
    "completeness": {"status": "complete", "missing": [], "conflicts": []},
}


class FakeMailClient:
    def __init__(self):
        self.messages = {}
        self.uid_validity = "777"
        self.saved = None
        self.aborted = False

    def connect(self, _address, _password):
        return None

    def get_messages(self):
        return []

    def get_details(self, _uid):
        long_body = "Bardzo długa wiadomość. " * 2000
        return long_body, [
            {"name": "Formularz.pdf", "part": object()},
            {"name": "Instrukcja.pdf", "part": object()},
        ]

    def save_attachment(self, attachment, path):
        self.saved = (attachment["name"], path)
        Path(path).write_bytes(b"%PDF-1.4\n% test")

    def get_indexed_attachment(self, _folder, _validity, _uid, index):
        return self.get_details("indexed")[1][index]

    def disconnect(self):
        return None

    def abort(self):
        self.aborted = True


class OfflineMailClient(FakeMailClient):
    def connect(self, _address, _password):
        raise OSError("getaddrinfo failed: technical-host-name")


class PreviewMailClient(FakeMailClient):
    def save_attachment(self, attachment, path):
        source = Path(create_test_pdf())
        try:
            Path(path).write_bytes(source.read_bytes())
        finally:
            source.unlink(missing_ok=True)


class JsonResponse(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class QtAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_settings(self):
        temporary = tempfile.TemporaryDirectory(dir=os.getcwd())
        settings = QSettings(os.path.join(temporary.name, "settings.ini"), QSettings.Format.IniFormat)
        state = AppSettings(settings)
        state.first_run_complete = True
        return temporary, state

    def test_extracted_logic_matches_reference_without_importing_tkinter(self):
        minimal = {
            "summary": "Instrukcje przygotowania do spotkania.",
            "events": [],
            "tasks": [],
            "warnings": [],
            "completeness": {"status": "uncertain", "missing": [], "conflicts": []},
        }
        self.assertEqual(
            core.validate_analysis(deepcopy(minimal), COMPLEX_BODY),
            reference.validate_analysis(deepcopy(minimal), COMPLEX_BODY),
        )
        package = Path(__file__).parent / "mailassistant_qt"
        self.assertNotIn("import tkinter", (package / "logic.py").read_text(encoding="utf-8"))
        self.assertNotIn("ai_assistant_04a", (package / "core.py").read_text(encoding="utf-8"))

    def test_progress_is_saved_per_message_and_account(self):
        temporary, state = self.make_settings()
        try:
            identity = "777|100|<mail@example.test>"
            state.save_completed_tasks("osoba@wp.pl", identity, ["krok-a", "krok-c"])
            self.assertEqual(state.completed_tasks("osoba@wp.pl", identity), {"krok-a", "krok-c"})
            self.assertEqual(state.completed_tasks("osoba@wp.pl", "778|100|<mail@example.test>"), set())
            self.assertEqual(state.completed_tasks("inna@wp.pl", identity), set())
        finally:
            state.settings.sync()
            del state
            gc.collect()
            temporary.cleanup()

    def test_message_identity_contains_uidvalidity_uid_and_message_id(self):
        client = FakeMailClient()
        message = EmailMessage()
        message["Message-ID"] = "<abc@example.test>"
        client.messages["100"] = message
        rows = core.enrich_message_rows(client, [{"uid": "100", "subject": "Test", "sender": "A"}])
        self.assertEqual(rows[0]["progress_id"], "777|100|<abc@example.test>")

    def test_task_progress_key_ignores_model_generated_id_and_wording(self):
        first = deepcopy(SAMPLE_ANALYSIS["tasks"][1])
        second = deepcopy(first)
        second["id"] = "completely-different-model-id"
        second["action"] = "Odeślij skan formularza"
        self.assertEqual(stable_task_key(first, 2), stable_task_key(second, 2))

    def test_task_checkboxes_and_step_navigation(self):
        page = TasksPage()
        first_key = stable_task_key(SAMPLE_ANALYSIS["tasks"][0], 1)
        page.set_analysis("42", SAMPLE_ANALYSIS, {first_key})
        self.assertTrue(page.checkboxes[first_key].isChecked())
        self.assertEqual(page.step_index, 0)
        page.next_step()
        self.assertEqual(page.step_index, 1)
        self.assertIn("Krok 2 z 4", page.step_counter.text())
        page.previous_step()
        self.assertEqual(page.step_index, 0)
        page.deleteLater()

    def test_dependencies_are_polish_and_hide_internal_ids(self):
        page = TasksPage()
        page.set_analysis("42", SAMPLE_ANALYSIS)
        text = page.step_text.text()
        page.next_step()
        text += page.step_text.text()
        self.assertIn("Najpierw wykonaj: Przygotuj dwa podpisane egzemplarze formularza", text)
        self.assertNotIn("task-1", text)
        page.deleteLater()

    def test_event_date_is_not_confirmation_deadline(self):
        page = TasksPage()
        page.set_analysis("42", SAMPLE_ANALYSIS)
        self.assertIn("03.11.2026, godz. 10:30", page.events_label.text())
        confirmation = SAMPLE_ANALYSIS["tasks"][2]
        self.assertEqual(confirmation["deadline"], "2026-10-30T12:00")
        self.assertNotEqual(confirmation["deadline"], SAMPLE_ANALYSIS["events"][0]["start"])
        page.deleteLater()

    def test_long_mail_and_multiple_pdf_names_are_emitted(self):
        service = MailService(FakeMailClient())
        received = []
        service.details_ready.connect(lambda uid, body, names: received.append((uid, body, names)))
        service.load_details("7")
        self.assertEqual(received[0][0], "7")
        self.assertGreater(len(received[0][1]), 20000)
        self.assertEqual(received[0][2], ["Formularz.pdf", "Instrukcja.pdf"])

    def test_pdf_attachment_is_really_written(self):
        client = FakeMailClient()
        service = MailService(client)
        service.load_details("7")
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            target = Path(directory) / "formularz.pdf"
            service.save_attachment("7", 0, str(target))
            self.assertTrue(target.is_file())
            self.assertTrue(target.read_bytes().startswith(b"%PDF"))

    def test_styles_support_larger_text_and_high_contrast(self):
        ensure_readable_font()
        normal = stylesheet(100, False)
        large = stylesheet(140, True)
        self.assertIn("font-size: 15px", normal)
        self.assertIn("font-size: 21px", large)
        self.assertIn("#000000", large)

    def test_unavailable_ollama_has_plain_polish_message(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            with self.assertRaisesRegex(RuntimeError, "Nie można połączyć się z lokalną Ollamą"):
                check_ollama()

    def test_wp_network_error_is_plain_and_details_stay_in_diagnostics(self):
        service = MailService(OfflineMailClient())
        errors = []
        diagnostics = []
        service.failed.connect(lambda area, message: errors.append((area, message)))
        service.diagnostic.connect(diagnostics.append)
        service.connect_and_load("osoba@wp.pl", "sekret")
        self.assertEqual(errors, [("poczta", "Brak połączenia z siecią. Sprawdź internet i spróbuj ponownie.")])
        self.assertIn("technical-host-name", diagnostics[0])
        self.assertNotIn("technical-host-name", errors[0][1])

    def test_unknown_error_is_hidden_from_user(self):
        message = core.user_error(RuntimeError("internal-object-id-123"))
        self.assertNotIn("internal-object-id-123", message)
        self.assertIn("diagnostyce", message)

    def test_ollama_check_rejects_missing_required_model(self):
        responses = [
            JsonResponse({"version": "1.2.3"}),
            JsonResponse({"models": [{"name": "inny-model:latest"}]}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "brakuje modelu qwen2.5:3b"):
                check_ollama()

    def test_responsive_pages_do_not_force_wide_layout(self):
        temporary, state = self.make_settings()
        window = MainWindow(state=state, mail_service=MailService(FakeMailClient()))
        try:
            window.resize(800, 650)
            window.show()
            window.show_page(2)
            self.app.processEvents()
            self.assertTrue(window.settings_page.address.isVisible())
            self.assertTrue(window.settings_page.password.isVisible())
            window._set_appearance(140, False)
            window.resize(760, 600)
            self.app.processEvents()
            self.assertGreaterEqual(window.sidebar.width(), 230)
            window._mail_connected = True
            window.show_page(0)
            window.mail_page.resize(600, 600)
            self.app.processEvents()
            self.assertEqual(window.mail_page.splitter.orientation(), Qt.Orientation.Horizontal)
            self.assertTrue(window.mail_page.compact_folder_row.isVisible())
        finally:
            window._shutdown_pending = False
            window._closing = False
            window.mail_thread.quit()
            window.mail_thread.wait(2000)
            window.deleteLater()
            self.app.processEvents()
            state.settings.sync()
            del window
            del state
            gc.collect()
            temporary.cleanup()

    def test_close_disconnects_asynchronously_without_blocking_ui(self):
        temporary, state = self.make_settings()
        window = MainWindow(state=state, mail_service=MailService(FakeMailClient()))
        try:
            event = QCloseEvent()
            started = time.monotonic()
            window.closeEvent(event)
            elapsed = time.monotonic() - started
            self.assertFalse(event.isAccepted())
            self.assertLess(elapsed, 0.5)
            deadline = time.monotonic() + 2.0
            while not window._allow_close and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertTrue(window._allow_close)
        finally:
            window.mail_thread.quit()
            window.mail_thread.wait(2000)
            window.deleteLater()
            self.app.processEvents()
            state.settings.sync()
            del window
            del state
            gc.collect()
            temporary.cleanup()

    def test_long_analysis_worker_keeps_signals_and_opens_tasks_page(self):
        temporary, state = self.make_settings()
        window = MainWindow(state=state, mail_service=MailService(FakeMailClient(), cache_root=temporary.name))

        def delayed_result():
            time.sleep(0.35)
            return deepcopy(SAMPLE_ANALYSIS)

        try:
            window._mail_connected = True
            window.mail_page.rows["long-mail"] = {
                "uid": "long-mail", "progress_id": "777|long-mail|<long@example.test>",
                "subject": "Długa wiadomość", "sender": "Anna",
            }
            with patch(
                "mailassistant_qt.main_window.create_analysis_worker",
                side_effect=lambda _subject, _body: FunctionWorker(delayed_result),
            ):
                window._analyze("long-mail", "Długa wiadomość", "Bardzo długa treść " * 5000)

            deadline = time.monotonic() + 3.0
            while (window._analysis_running or window._active_workers) and time.monotonic() < deadline:
                gc.collect()
                self.app.processEvents()
                time.sleep(0.02)

            self.assertFalse(window._analysis_running)
            self.assertEqual(window.stack.currentWidget(), window.tasks_page)
            self.assertIn("Przygotuj dokumenty", window.tasks_page.summary.text())
            self.assertEqual(len(window._active_workers), 0)
        finally:
            window.mail_thread.quit()
            window.mail_thread.wait(2000)
            window.deleteLater()
            self.app.processEvents()
            state.settings.sync()
            del window
            del state
            gc.collect()
            temporary.cleanup()

    def test_document_preview_click_completes_through_mail_thread(self):
        temporary, state = self.make_settings()
        state.address = "konto@wp.pl"
        client = PreviewMailClient()
        window = MainWindow(state=state, mail_service=MailService(client, cache_root=temporary.name))
        try:
            window._mail_connected = True
            window.documents_page.set_rows([{
                "id": "document-1", "name": "Main_CV.pdf", "local_path": "",
                "server_available": True, "folder": "INBOX", "uid_validity": "1792763100",
                "server_uid": "4", "mail_ref": "mail:old-session", "attachment_index": 0,
                "subject": "test", "category": "received",
            }])
            with patch.object(window, "_index_pdf_background"):
                window.documents_page.open_button.click()
                self.assertEqual(window.documents_page.open_button.text(), "Pobieranie PDF…")
                deadline = time.monotonic() + 3.0
                while window.stack.currentWidget() is not window.document_preview_page and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
            self.assertIs(window.stack.currentWidget(), window.document_preview_page)
            self.assertGreaterEqual(window.document_preview_page.pdf_document.pageCount(), 1)
            self.assertEqual(window.document_preview_page.title.text(), "Main_CV.pdf")
            self.assertEqual(window.documents_page.open_button.text(), "Otwórz podgląd PDF")
            self.assertEqual(window._pending_document_action, "")
        finally:
            window.document_preview_page.close_pdf()
            window.mail_thread.quit()
            window.mail_thread.wait(2000)
            window.deleteLater()
            self.app.processEvents()
            state.settings.sync()
            del window
            del state
            gc.collect()
            temporary.cleanup()

    def test_saved_address_does_not_reveal_account_pages_before_login(self):
        temporary, state = self.make_settings()
        state.address = "zapamietany@wp.pl"
        window = MainWindow(state=state, mail_service=MailService(FakeMailClient()))
        try:
            window.show()
            self.app.processEvents()
            self.assertFalse(window._mail_connected)
            self.assertIs(window.stack.currentWidget(), window.home_page)
            self.assertEqual(window.mail_page.all_rows, [])
            self.assertEqual(window.documents_page.rows, {})
            for index in (0, 1, 3, 4, 5, 6):
                window.show_page(index)
                self.assertIs(window.stack.currentWidget(), window.login_required_page)
            window.login_required_page.login_requested.emit()
            self.app.processEvents()
            self.assertIs(window.stack.currentWidget(), window.settings_page)
            self.assertTrue(window.settings_page.password.hasFocus())
        finally:
            window.mail_thread.quit()
            window.mail_thread.wait(2000)
            window.deleteLater()
            self.app.processEvents()
            temporary.cleanup()

    def test_successful_login_commits_address_and_opens_mail(self):
        temporary, state = self.make_settings()
        state.address = "stare@wp.pl"
        window = MainWindow(state=state, mail_service=MailService(FakeMailClient()))
        try:
            window._pending_address = "nowe@wp.pl"
            window._pending_password = "sekret"
            window._messages_loaded([])
            self.assertTrue(window._mail_connected)
            self.assertEqual(window.account_address, "nowe@wp.pl")
            self.assertEqual(state.address, "nowe@wp.pl")
            self.assertEqual(window._session_password, "sekret")
            self.assertIs(window.stack.currentWidget(), window.mail_page)
        finally:
            window.mail_thread.quit()
            window.mail_thread.wait(2000)
            window.deleteLater()
            self.app.processEvents()
            temporary.cleanup()

    def test_refresh_adds_only_new_messages_without_duplicates(self):
        page = MailPage()
        page.set_mailbox({
            "folder": "INBOX", "rows": [{"uid": "mail-1", "subject": "Pierwsza"}],
            "total": 1, "offset": 0, "limit": 50, "query": "",
        })
        added = page.set_refresh_result({
            "folder": "INBOX",
            "rows": [
                {"uid": "mail-2", "subject": "Nowa"},
                {"uid": "mail-1", "subject": "Pierwsza poprawiona"},
                {"uid": "mail-2", "subject": "Duplikat z serwera"},
            ],
            "total": 2, "offset": 0, "limit": 50, "query": "",
        })
        self.assertEqual(added, 1)
        self.assertEqual([row["uid"] for row in page.all_rows], ["mail-2", "mail-1"])
        self.assertEqual(page.all_rows[1]["subject"], "Pierwsza poprawiona")
        self.assertIn("Dodano 1", page.status.text())
        self.assertEqual(page.set_refresh_result({
            "folder": "INBOX", "rows": list(page.all_rows), "total": 2,
            "offset": 0, "limit": 50, "query": "",
        }), 0)
        self.assertEqual(page.message_list.count(), 2)
        self.assertEqual(page.status.text(), "Brak nowych wiadomości.")
        page.deleteLater()

    def test_mail_service_refresh_has_separate_result_and_classifies_connection_loss(self):
        class RefreshClient(FakeMailClient):
            def load_folder(self, folder, offset, limit, query):
                return {
                    "folder": folder, "rows": [{"uid": "mail-2", "subject": "Nowa"}],
                    "total": 1, "offset": offset, "limit": limit, "query": query,
                }

        client = RefreshClient()
        service = MailService(client)
        service._folders = [{"id": "INBOX", "role": "inbox"}]
        refreshed = []
        failures = []
        service.mailbox_refreshed.connect(refreshed.append)
        service.refresh_failed.connect(lambda message, lost: failures.append((message, lost)))
        service.refresh_mailbox("INBOX", 50, "")
        self.assertEqual(refreshed[0]["rows"][0]["uid"], "mail-2")
        self.assertEqual(failures, [])

        client.load_folder = lambda *_args: (_ for _ in ()).throw(OSError("connection reset"))
        service.refresh_mailbox("INBOX", 50, "")
        self.assertTrue(failures[-1][1])

    def test_refresh_reconnects_once_then_hides_account_data(self):
        temporary, state = self.make_settings()
        window = MainWindow(state=state, mail_service=MailService(FakeMailClient()))
        try:
            window._mail_connected = True
            window.account_address = "konto@wp.pl"
            window._session_password = "tylko-w-pamieci"
            window._refresh_running = True
            window._refresh_request = ("INBOX", 50, "")
            reconnects = []
            window.connect_mail.connect(lambda address, password: reconnects.append((address, password)))
            window._mailbox_refresh_failed("Połączenie zostało przerwane.", True)
            self.assertEqual(reconnects, [("konto@wp.pl", "tylko-w-pamieci")])
            self.assertTrue(window._reconnecting_for_refresh)
            self.assertTrue(window._refresh_retry_used)
            with patch.object(QMessageBox, "warning"):
                window._mailbox_refresh_failed("Połączenie nadal nie działa.", True)
            self.assertFalse(window._mail_connected)
            self.assertEqual(window._session_password, "")
            self.assertIs(window.stack.currentWidget(), window.login_required_page)
        finally:
            window.mail_thread.quit()
            window.mail_thread.wait(2000)
            window.deleteLater()
            self.app.processEvents()
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()

