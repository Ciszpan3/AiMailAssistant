"""Regresja UX/UI po redesignie Testu 10B."""

import gc
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton, QScrollArea

from mailassistant_qt.documents_ui import DocumentListRow, DocumentPreviewPage, DocumentsPage
from mailassistant_qt.documents import DocumentIndex
from mailassistant_qt.main_window import MailListRow, MailPage, MainWindow, MessagePage, TasksPage, friendly_error
from mailassistant_qt.reply_ui import ReplyPage, ReviewDialog
from mailassistant_qt.semantic_ui import SemanticResultCard, SemanticSearchPage
from mailassistant_qt.senior_ui import FirstRunWizard, HelpPage, HomePage
from mailassistant_qt.state import AppSettings
from mailassistant_qt.styles import stylesheet
from mailassistant_qt.workers import MailService


class MinimalMailClient:
    uid_validity = "10"

    def connect(self, _address, _password):
        return None

    def get_messages(self):
        return []

    def disconnect(self):
        return None

    def abort(self):
        return None


class Test10BRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        temporary = tempfile.TemporaryDirectory(dir=os.getcwd())
        settings = QSettings(str(Path(temporary.name) / "settings.ini"), QSettings.Format.IniFormat)
        state = AppSettings(settings)
        state.first_run_complete = True
        window = MainWindow(state=state, mail_service=MailService(MinimalMailClient()))
        return temporary, window

    def close_window(self, temporary, window):
        window.mail_thread.quit()
        window.mail_thread.wait(2000)
        window.deleteLater()
        self.app.processEvents()
        QTest.qWait(10)
        gc.collect()
        temporary.cleanup()

    def test_01_visual_system_has_focus_hierarchy_and_statuses(self):
        value = stylesheet(120, False)
        self.assertIn("QPushButton:focus", value)
        self.assertIn("QPushButton#QuietButton", value)
        self.assertIn("QLabel#StatusSuccess", value)
        self.assertIn("QFrame#Card", value)

    def test_02_navigation_is_single_and_human_readable(self):
        temporary, window = self.make_window()
        try:
            labels = [window.nav_by_index[index].text() for index in (
                window.HOME_PAGE, 0, 1, 4, window.SEMANTIC_PAGE, window.HELP_PAGE,
            )]
            self.assertEqual(labels, ["Start", "Poczta", "Moje zadania", "Moje dokumenty", "Szukaj", "Pomoc"])
            self.assertEqual(window.nav_by_index[2].text(), "Ustawienia")
            self.assertNotIn("Odpowiedź", [button.text() for button in window.nav_buttons])
        finally:
            self.close_window(temporary, window)

    def test_03_detail_pages_keep_parent_navigation_selected(self):
        temporary, window = self.make_window()
        try:
            window._mail_connected = True
            window.show_page(5)
            self.assertTrue(window.nav_by_index[0].isChecked())
            window.show_page(6)
            self.assertTrue(window.nav_by_index[4].isChecked())
        finally:
            self.close_window(temporary, window)

    def test_04_help_page_has_direct_safe_actions(self):
        page = HelpPage()
        labels = [button.text() for button in page.findChildren(QPushButton)]
        self.assertIn("Otwórz pocztę", labels)
        self.assertIn("Zobacz moje zadania", labels)
        self.assertIn("Znajdź dokument", labels)
        self.assertIn("Znajdź wiadomość po opisie", labels)
        page.deleteLater()

    def test_05_home_retains_four_large_primary_choices(self):
        page = HomePage()
        self.assertEqual(len(page.action_buttons), 4)
        self.assertTrue(all(button.minimumHeight() >= 100 for button in page.action_buttons))
        self.assertEqual(page.findChild(QPushButton, "missing"), None)
        page.deleteLater()

    def test_06_compact_mail_uses_folder_picker_and_keeps_list_visible(self):
        page = MailPage()
        page.set_folders([{"id": "INBOX", "name": "Odebrane", "role": "inbox"}])
        self.assertEqual(page.folder_list.item(0).data(Qt.ItemDataRole.UserRole), "__ALL__")
        self.assertEqual(page.folder_list.item(0).text(), "Wszystkie wiadomości")
        self.assertEqual(page.folder_list.item(1).text(), "Odebrane")
        page.resize(700, 620)
        page.show()
        self.app.processEvents()
        self.assertTrue(page.compact_folder_row.isVisible())
        self.assertFalse(page.folder_card.isVisible())
        self.assertTrue(page.mail_list_card.isVisible())
        self.assertFalse(page.filter_panel.isVisible())
        page.close()
        page.deleteLater()

    def test_07_mail_rows_show_sender_subject_date_snippet_and_attachment(self):
        page = MailPage()
        page.set_messages([{
            "uid": "1", "sender": "Anna Kowalska", "subject": "Bardzo długi temat wiadomości",
            "date": "29.10.2026", "snippet": "Czytelny fragment treści", "unread": True,
            "has_attachments": True,
        }])
        item = page.message_list.item(0)
        self.assertIsNone(page.message_list.itemWidget(item, 0))
        self.assertIn("Nowa", item.text(3))
        self.assertIn("Załącznik PDF", item.text(3))
        accessible = str(item.data(0, Qt.ItemDataRole.AccessibleTextRole) or "")
        self.assertIn("Anna Kowalska", accessible)
        self.assertIn("Załącznik PDF", accessible)
        page.deleteLater()

    def test_08_message_uses_plain_primary_actions_and_scrolls(self):
        page = MessagePage()
        page.show_message(
            "1", {"subject": "Temat", "sender": "Anna", "recipient": "Jan", "date": "29.10.2026"},
            "Długa treść. " * 300, ["formularz.pdf"],
        )
        self.assertEqual(page.reply_button.text(), "Odpowiedz")
        self.assertEqual(page.analyze_button.text(), "Wyjaśnij wiadomość")
        page.resize(600, 480)
        page.show()
        self.app.processEvents()
        self.assertGreater(page.scroll.verticalScrollBar().maximum(), 0)
        page.close()
        page.deleteLater()

    def test_09_tasks_offer_return_to_source_and_step_navigation(self):
        page = TasksPage()
        page.set_analysis("plan", {"summary": "Plan", "tasks": [{"id": "a", "action": "Podpisz formularz"}]}, mail_uid="1")
        self.assertFalse(page.source_button.isHidden())
        self.assertIn("Krok 1 z 1", page.step_counter.text())
        page.deleteLater()

    def test_10_document_rows_explain_origin_and_type(self):
        page = DocumentsPage()
        page.set_rows([{
            "id": "d1", "name": "Formularz.pdf", "category": "received", "document_date": "29.10.2026",
            "source_count": 1, "local_path": "", "favorite": False,
        }])
        item = page.result_list.item(0)
        self.assertIsInstance(page.result_list.itemWidget(item), DocumentListRow)
        self.assertIn("otrzymany", str(item.data(Qt.ItemDataRole.AccessibleTextRole) or ""))
        page.deleteLater()

    def test_11_pdf_preview_has_four_understandable_actions(self):
        page = DocumentPreviewPage()
        labels = [button.text() for button in page.findChildren(QPushButton)]
        self.assertIn("← Wróć do dokumentów", labels)
        self.assertIn("Drukuj", labels)
        self.assertIn("Zapisz kopię", labels)
        self.assertIn("Otwórz wiadomość", labels)
        page.deleteLater()

    def test_12_reply_fields_have_accessible_names_and_one_send_review_action(self):
        page = ReplyPage()
        self.assertEqual(page.recipient.accessibleName(), "Adres odbiorcy")
        self.assertEqual(page.subject.accessibleName(), "Temat wiadomości")
        self.assertEqual(page.body.accessibleName(), "Treść wiadomości")
        self.assertEqual(page.review_button.text(), "Sprawdź przed wysłaniem")
        page.deleteLater()

    def test_13_review_dialog_names_recipient_attachments_and_explicit_send(self):
        dialog = ReviewDialog({"to": "anna@example.pl", "subject": "Temat", "body": "Treść", "attachments": []}, [])
        labels = [button.text() for button in dialog.findChildren(QPushButton)]
        self.assertEqual(dialog.windowTitle(), "Sprawdź wiadomość przed wysłaniem")
        self.assertIn("Wróć do edycji", labels)
        self.assertIn("Wyślij wiadomość", labels)
        dialog.deleteLater()

    def test_14_technical_errors_are_replaced_with_plain_guidance(self):
        value = friendly_error("poczta", "IMAP socket WinError traceback")
        self.assertIn("Nie udało się połączyć z pocztą", value)
        self.assertNotIn("IMAP", value)

    def test_15_wizard_pages_scroll_on_large_text_or_small_screen(self):
        wizard = FirstRunWizard()
        self.assertTrue(all(isinstance(wizard.stack.widget(index), QScrollArea) for index in range(wizard.stack.count())))
        wizard.deleteLater()

    def test_16_main_window_can_shrink_for_scaled_laptop_screen(self):
        temporary, window = self.make_window()
        try:
            self.assertLessEqual(window.minimumWidth(), 680)
            self.assertLessEqual(window.minimumHeight(), 520)
            window._set_appearance(140, True)
            window.resize(760, 600)
            window.show()
            self.app.processEvents()
            self.assertGreaterEqual(window.sidebar.width(), 230)
        finally:
            self.close_window(temporary, window)

    def test_17_semantic_search_keeps_query_after_results(self):
        page = SemanticSearchPage()
        page.query.setPlainText("Kowalska prosiła o formularz")
        page.show_results({"results": [], "indexed": 10, "complete": True})
        self.assertEqual(page.query.toPlainText(), "Kowalska prosiła o formularz")
        self.assertEqual(page.query.accessibleName(), "Opis szukanej wiadomości")
        page.deleteLater()

    def test_18_opening_search_result_returns_to_same_results(self):
        temporary, window = self.make_window()
        try:
            window._mail_connected = True
            window.semantic_page.query.setPlainText("spotkanie z Kowalską")
            window.mail_page.rows["1"] = {"uid": "1", "subject": "Spotkanie", "sender": "Anna"}
            window.mail_page.bodies_by_uid["1"] = "Zaproszenie na spotkanie."
            window.show_page(window.SEMANTIC_PAGE)
            window._open_semantic_result({"uid": "1"})
            self.assertIs(window.stack.currentWidget(), window.message_page)
            window._return_from_message()
            self.app.processEvents()
            self.assertIs(window.stack.currentWidget(), window.semantic_page)
            self.assertEqual(window.semantic_page.query.toPlainText(), "spotkanie z Kowalską")
        finally:
            self.close_window(temporary, window)

    def test_19_dangerous_draft_delete_is_visually_distinct(self):
        page = ReplyPage()
        self.assertEqual(page.delete_button.objectName(), "DangerButton")
        page.deleteLater()

    def test_20_search_and_mail_inputs_have_accessible_names(self):
        mail = MailPage()
        semantic = SemanticSearchPage()
        documents = DocumentsPage()
        self.assertEqual(mail.search.accessibleName(), "Szukaj wiadomości")
        self.assertEqual(semantic.query.accessibleName(), "Opis szukanej wiadomości")
        self.assertEqual(documents.query.accessibleName(), "Szukaj w dokumentach")
        mail.deleteLater()
        semantic.deleteLater()
        documents.deleteLater()

    def test_21_all_messages_loads_folder_contents_without_search_text(self):
        class FolderClient(MinimalMailClient):
            def __init__(self):
                self.messages = {}

            def load_folder(self, folder, offset, limit, query):
                return {
                    "folder": folder,
                    "rows": [{"uid": f"{folder}-1", "subject": f"Wiadomość {folder}"}],
                    "total": 1, "offset": offset, "limit": limit, "query": query,
                }

        service = MailService(FolderClient())
        service._folders = [
            {"id": "INBOX", "role": "inbox"},
            {"id": "Sent", "role": "sent"},
            {"id": "Trash", "role": "trash"},
        ]
        result = service._load_mailbox_value("__ALL__", 0, 50, "")
        self.assertEqual({row["uid"] for row in result["rows"]}, {"INBOX-1", "Sent-1"})
        self.assertEqual(result["total"], 2)

    def test_22_custom_rows_do_not_draw_hidden_legacy_text(self):
        mail = MailPage()
        mail.set_messages([{"uid": "1", "sender": "Anna", "subject": "Temat", "date": "Dzisiaj"}])
        self.assertEqual(mail.message_list.item(0).text(1), "Temat")
        documents = DocumentsPage()
        documents.set_rows([{"id": "d1", "name": "Plik.pdf", "category": "received"}])
        self.assertEqual(documents.result_list.item(0).text(), "")
        mail.deleteLater()
        documents.deleteLater()

    def test_23_mail_rows_keep_a_stable_compact_height(self):
        mail = MailPage()
        mail.set_messages([{
            "uid": "1", "sender": "Bardzo długi nadawca <adres@example.pl>",
            "subject": "Długi temat wymagający zawinięcia", "date": "29.10.2026",
            "snippet": "Dłuższy fragment wiadomości.", "unread": True, "has_attachments": True,
        }])
        mail.resize(520, 620)
        mail.show()
        self.app.processEvents()
        item = mail.message_list.item(0)
        self.assertEqual(item.sizeHint(0).height(), 52)
        self.assertIsNone(mail.message_list.itemWidget(item, 0))
        mail.close()
        mail.deleteLater()

    def test_24_wrapped_document_origin_increases_card_height(self):
        page = DocumentsPage()
        page.set_rows([{
            "id": "d1", "name": "Bardzo_długa_nazwa_formularza.pdf",
            "category": "received,sent", "source_count": 2, "document_date": "29.10.2026",
        }])
        page.resize(900, 650)
        page.show()
        self.app.processEvents()
        item = page.result_list.item(0)
        widget = page.result_list.itemWidget(item)
        required = widget.layout().heightForWidth(widget.width())
        self.assertGreaterEqual(item.sizeHint().height(), required)
        page.close()
        page.deleteLater()

    def test_25_mail_rows_are_compact_and_use_one_line_on_wide_view(self):
        page = MailPage()
        page.set_messages([
            {
                "uid": str(index), "sender": f"Nadawca {index}",
                "subject": f"Temat wiadomości {index}",
                "date": "Dzisiaj, 10:30", "snippet": "Krótki fragment treści wiadomości",
                "unread": index % 2 == 0, "has_attachments": index % 3 == 0,
            }
            for index in range(12)
        ])
        page.resize(1100, 720)
        page.show()
        self.app.processEvents()
        heights = [page.message_list.item(index).sizeHint(0).height() for index in range(12)]
        self.assertTrue(all(48 <= height <= 72 for height in heights))
        self.assertIn("Krótki fragment", page.message_list.item(0).text(2))
        page.close()
        page.deleteLater()

    def test_26_sidebar_can_collapse_expand_and_remember_state(self):
        temporary, window = self.make_window()
        try:
            window.resize(1100, 720)
            window.show()
            self.app.processEvents()
            window.sidebar_toggle.click()
            self.app.processEvents()
            self.assertLessEqual(window.sidebar.width(), 72)
            self.assertEqual(window.nav_by_index[0].text(), "")
            self.assertEqual(window.nav_by_index[0].toolTip(), "Poczta")
            self.assertTrue(window.state.sidebar_collapsed)
            window.sidebar_toggle.click()
            self.app.processEvents()
            self.assertGreaterEqual(window.sidebar.width(), 205)
            self.assertEqual(window.nav_by_index[0].text(), "Poczta")
            self.assertFalse(window.state.sidebar_collapsed)
        finally:
            self.close_window(temporary, window)

    def test_27_mail_history_has_clear_two_way_pagination(self):
        page = MailPage()
        requests = []
        page.mailbox_requested.connect(lambda *args: requests.append(args))
        page.set_mailbox({
            "folder": "INBOX", "offset": 0, "limit": 50, "total": 123, "query": "",
            "rows": [{"uid": f"new-{index}", "subject": f"Nowa {index}"} for index in range(50)],
        })
        self.assertIn("1–50 z 123", page.pagination_label.text())
        self.assertIn("strona 1 z 3", page.pagination_label.text())
        self.assertFalse(page.newer_button.isEnabled())
        self.assertTrue(page.older_button.isEnabled())
        page.older_button.click()
        self.assertEqual(requests[-1], ("INBOX", 50, 50, ""))

        page.set_mailbox({
            "folder": "INBOX", "offset": 50, "limit": 50, "total": 123, "query": "",
            "rows": [{"uid": f"old-{index}", "subject": f"Starsza {index}"} for index in range(50)],
        })
        self.assertEqual(len(page.all_rows), 50)
        self.assertIn("51–100 z 123", page.pagination_label.text())
        self.assertTrue(page.newer_button.isEnabled())
        page.newer_button.click()
        self.assertEqual(requests[-1], ("INBOX", 0, 50, ""))
        page.deleteLater()

    def test_28_page_size_is_explicit_and_reloads_from_newest(self):
        page = MailPage()
        page.set_mailbox({
            "folder": "INBOX", "offset": 50, "limit": 50, "total": 200, "query": "",
            "rows": [{"uid": str(index)} for index in range(50)],
        })
        requests = []
        page.mailbox_requested.connect(lambda *args: requests.append(args))
        page.page_size.setCurrentIndex(page.page_size.findData(25))
        self.assertEqual(requests[-1], ("INBOX", 0, 25, ""))
        page.deleteLater()

    def test_29_all_messages_respects_requested_history_page(self):
        class ManyFolderClient(MinimalMailClient):
            def __init__(self):
                self.messages = {}

            def load_folder(self, folder, offset, limit, query):
                total = 80
                end = min(total, offset + limit)
                return {
                    "folder": folder, "total": total, "offset": offset,
                    "limit": limit, "query": query,
                    "rows": [
                        {"uid": f"{folder}-{index}", "subject": f"Wiadomość {index}"}
                        for index in range(offset, end)
                    ],
                }

        service = MailService(ManyFolderClient())
        service._folders = [
            {"id": "INBOX", "role": "inbox"},
            {"id": "Sent", "role": "sent"},
        ]
        result = service._load_mailbox_value("__ALL__", 50, 25, "")
        self.assertEqual(result["offset"], 50)
        self.assertEqual(result["total"], 160)
        self.assertEqual(len(result["rows"]), 25)

    def test_30_mail_status_and_date_are_drawn_by_one_delegate(self):
        page = MailPage()
        page.set_messages([{
            "uid": "1", "sender": "Anna", "subject": "Dokumenty",
            "date": "26.10.2026, 10:42", "snippet": "Treść wiadomości",
            "unread": True, "has_attachments": True,
        }])
        page.resize(1100, 500)
        page.show()
        self.app.processEvents()
        item = page.message_list.item(0)
        self.assertIn("Nowa", item.text(3))
        self.assertIn("Załącznik PDF", item.text(3))
        self.assertIn("26.10.2026, 10:42", item.text(3))
        self.assertIsNone(page.message_list.itemWidget(item, 0))
        page.close()
        page.deleteLater()

    def test_31_switching_pages_does_not_create_top_level_mail_rows(self):
        page = MailPage()
        page.resize(1000, 600)
        page.show()
        for offset in (0, 50, 100):
            page.set_mailbox({
                "folder": "INBOX", "offset": offset, "limit": 50, "total": 150, "query": "",
                "rows": [
                    {"uid": f"{offset}-{index}", "sender": "Anna", "subject": "Temat"}
                    for index in range(50)
                ],
            })
            self.app.processEvents()
        floating_rows = [widget for widget in self.app.topLevelWidgets() if isinstance(widget, MailListRow)]
        self.assertEqual(floating_rows, [])
        page.close()
        page.deleteLater()

    def test_32_login_reset_does_not_detach_custom_rows_as_windows(self):
        documents = DocumentsPage()
        documents.set_rows([{"id": "d1", "name": "Formularz.pdf", "category": "received"}])
        semantic = SemanticSearchPage()
        semantic.show_results({
            "results": [{"uid": "m1", "subject": "Spotkanie", "sender": "Anna"}],
            "complete": True,
        })
        documents.show()
        semantic.show()
        self.app.processEvents()
        documents.reset()
        semantic.reset()
        self.app.processEvents()
        floating = [
            widget for widget in self.app.topLevelWidgets()
            if isinstance(widget, (DocumentListRow, SemanticResultCard, MailListRow))
        ]
        self.assertEqual(floating, [])
        documents.close()
        semantic.close()
        documents.deleteLater()
        semantic.deleteLater()

    def test_33_documents_have_two_way_pagination_and_visible_loading_state(self):
        page = DocumentsPage()
        requests = []
        page.search_requested.connect(lambda *args: requests.append(args))
        page.set_page({
            "rows": [{"id": f"d{index}", "name": f"Dokument {index}.pdf"} for index in range(25)],
            "total": 61, "offset": 0, "limit": 25,
        })
        self.assertIn("1–25 z 61", page.pagination_label.text())
        self.assertTrue(page.older_button.isEnabled())
        page.older_button.click()
        self.assertEqual(requests[-1][2:4], (25, 25))
        self.assertTrue(requests[-1][4])
        self.assertEqual(page.older_button.text(), "Pobieranie starszych…")
        self.assertFalse(page.older_button.isEnabled())
        page.deleteLater()

    def test_34_mail_search_defaults_to_entire_server_mailbox(self):
        page = MailPage()
        requests = []
        page.mailbox_requested.connect(lambda *args: requests.append(args))
        self.assertEqual(page.search_scope.currentData(), "all")
        page.search.setText("Kowalska formularz")
        page.search_button.click()
        self.assertEqual(requests[-1], ("__ALL__", 0, 50, "Kowalska formularz"))
        self.assertIn("całej skrzynce", page.status.text())
        page.deleteLater()

    def test_35_document_index_returns_all_local_pages(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            index = DocumentIndex(str(Path(directory) / "documents.sqlite3"))
            for number in range(63):
                index.upsert_document("konto@example.pl", {
                    "source_key": f"doc-{number}", "name": f"Formularz {number:03d}.pdf",
                    "category": "received", "document_date": f"2026-10-{(number % 28) + 1:02d}",
                })
            first = index.search_page("konto@example.pl", "Formularz", "all", 0, 25)
            last = index.search_page("konto@example.pl", "Formularz", "all", 50, 25)
            self.assertEqual(first["total"], 63)
            self.assertEqual(len(first["rows"]), 25)
            self.assertEqual(last["offset"], 50)
            self.assertEqual(len(last["rows"]), 13)

    def test_36_whole_mailbox_document_scan_reaches_oldest_page(self):
        class DocumentMailboxClient(MinimalMailClient):
            def __init__(self):
                self.messages = {}

            def load_folder(self, folder, offset, limit, query):
                total = 151
                end = min(total, offset + limit)
                rows = []
                for number in range(offset, end):
                    names = ["bardzo_stary_formularz.pdf"] if number == 150 else []
                    rows.append({
                        "uid": f"mail-{number}", "server_uid": str(number + 1),
                        "folder": folder, "uid_validity": "10",
                        "subject": f"Wiadomość {number}", "attachment_names": names,
                        "has_attachments": bool(names), "date": f"2026-10-{(number % 28) + 1:02d}",
                    })
                return {
                    "folder": folder, "rows": rows, "total": total,
                    "offset": offset, "limit": limit, "query": query,
                }

        temporary = tempfile.TemporaryDirectory(dir=os.getcwd())
        settings = QSettings(str(Path(temporary.name) / "settings.ini"), QSettings.Format.IniFormat)
        state = AppSettings(settings)
        state.first_run_complete = True
        window = MainWindow(state=state, mail_service=MailService(DocumentMailboxClient()))
        try:
            window.document_index = DocumentIndex(str(Path(temporary.name) / "documents.sqlite3"))
            window.account_address = "konto@example.pl"
            window._mail_connected = True
            window.mail_page.set_folders([{"id": "INBOX", "name": "Odebrane", "role": "inbox"}])
            window._search_documents("bardzo_stary", "all", 0, 25, True)
            for _ in range(300):
                self.app.processEvents()
                if not window._document_scan_active and window.documents_page.total == 1:
                    break
                time.sleep(0.01)
            self.assertTrue(window._document_scan_complete)
            self.assertEqual(window._document_scan_seen, 151)
            self.assertEqual(window.documents_page.total, 1)
            self.assertEqual(window.documents_page.result_list.count(), 1)
        finally:
            window.thread_pool.waitForDone(5000)
            self.close_window(temporary, window)


if __name__ == "__main__":
    unittest.main()
