import gc
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from mailassistant_qt.main_window import MainWindow
from mailassistant_qt.senior_ui import FirstRunWizard, HomePage
from mailassistant_qt.state import AppSettings
from mailassistant_qt.workers import MailService, check_ollama_analysis


class MinimalMailClient:
    def __init__(self):
        self.messages = {}
        self.uid_validity = "10"

    def connect(self, _address, _password):
        return None

    def get_messages(self):
        return []

    def disconnect(self):
        return None

    def abort(self):
        return None


class JsonResponse(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class Test10SeniorModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_state(self, complete=False):
        temporary = tempfile.TemporaryDirectory(dir=os.getcwd())
        settings = QSettings(str(Path(temporary.name) / "settings.ini"), QSettings.Format.IniFormat)
        state = AppSettings(settings)
        state.first_run_complete = complete
        return temporary, state

    def close_window(self, window, temporary):
        window.mail_thread.quit()
        window.mail_thread.wait(2000)
        window.deleteLater()
        self.app.processEvents()
        gc.collect()
        temporary.cleanup()

    def test_01_first_start_opens_six_step_wizard(self):
        temporary, state = self.make_state(False)
        window = MainWindow(state=state, mail_service=MailService(MinimalMailClient()))
        try:
            self.assertTrue(window._wizard_active)
            self.assertIs(window.stack.currentWidget(), window.first_run_wizard)
            self.assertFalse(window.sidebar.isVisible())
            self.assertEqual(window.first_run_wizard.stack.count(), 6)
            self.assertEqual(window.first_run_wizard.next_button.text(), "Rozpocznij")
        finally:
            self.close_window(window, temporary)

    def test_02_home_has_four_clear_primary_actions(self):
        home = HomePage()
        buttons = [home.mail_button, home.tasks_button, home.documents_button, home.compose_button]
        self.assertEqual(len(buttons), 4)
        self.assertTrue(all(button.minimumHeight() >= 100 for button in buttons))
        self.assertIn("Sprawdź pocztę", home.mail_button.text())
        self.assertIn("Moje zadania", home.tasks_button.text())
        self.assertIn("Znajdź dokument", home.documents_button.text())
        self.assertIn("Napisz wiadomość", home.compose_button.text())
        self.assertTrue(all(button.focusPolicy() != Qt.FocusPolicy.NoFocus for button in buttons))
        home.deleteLater()

    def test_03_home_summary_shows_unread_tasks_deadline_and_continue(self):
        home = HomePage()
        home.set_summary(True, 3, 2, "30.10.2026, 12:00", "Potwierdź udział")
        self.assertIn("3", home.unread.text())
        self.assertIn("2", home.tasks.text())
        self.assertIn("30.10.2026", home.deadline.text())
        self.assertIn("Potwierdź udział", home.last_task.text())
        self.assertFalse(home.continue_button.isHidden())
        home.deleteLater()

    def test_04_simple_and_advanced_modes_do_not_change_saved_progress(self):
        temporary, state = self.make_state(True)
        state.save_completed_tasks("konto@wp.pl", "mail-1", ["krok-1"])
        window = MainWindow(state=state, mail_service=MailService(MinimalMailClient()))
        try:
            window._set_simple_mode(True)
            self.assertTrue(state.simple_mode)
            self.assertTrue(window.nav_by_index[2].isHidden())
            window._set_simple_mode(False)
            self.assertFalse(state.simple_mode)
            self.assertFalse(window.nav_by_index[2].isHidden())
            self.assertEqual(state.completed_tasks("konto@wp.pl", "mail-1"), {"krok-1"})
        finally:
            self.close_window(window, temporary)

    def test_05_wizard_can_go_back_and_reports_incomplete_setup(self):
        wizard = FirstRunWizard()
        wizard.next_step()
        wizard.next_step()
        self.assertEqual(wizard.stack.currentIndex(), 2)
        wizard.previous_step()
        self.assertEqual(wizard.stack.currentIndex(), 1)
        while wizard.stack.currentIndex() < 5:
            wizard.next_step()
        self.assertIn("Poczta wymaga połączenia", wizard.finish_summary.text())
        self.assertIn("Asystent AI wymaga konfiguracji", wizard.finish_summary.text())
        wizard.deleteLater()

    def test_06_finishing_wizard_saves_preferences_and_opens_mail_login(self):
        temporary, state = self.make_state(False)
        window = MainWindow(state=state, mail_service=MailService(MinimalMailClient()))
        try:
            window._finish_first_run_wizard({
                "font_scale": 120, "high_contrast": True, "simple_mode": True,
                "default_printer": "Drukarka A", "default_scanner": "Skaner A",
            })
            self.assertTrue(state.first_run_complete)
            self.assertEqual(state.font_scale, 120)
            self.assertTrue(state.high_contrast)
            self.assertTrue(state.simple_mode)
            self.assertEqual(state.default_printer, "Drukarka A")
            self.assertIs(window.stack.currentWidget(), window.login_required_page)
        finally:
            self.close_window(window, temporary)

    def test_07_wizard_device_choice_and_status_are_plain(self):
        wizard = FirstRunWizard()
        wizard.set_devices({
            "printers": [{"name": "Domowa drukarka"}],
            "scanners": [{"name": "Domowy skaner", "id": "scan-1"}],
        })
        self.assertEqual(wizard.printer_combo.currentText(), "Nie wybrano")
        self.assertEqual(wizard.printer_combo.itemText(1), "Domowa drukarka")
        self.assertIn("Znaleziono drukarki: 1", wizard.devices_status.text())
        wizard.deleteLater()

    def test_08_ollama_wizard_check_runs_real_test_request(self):
        responses = [
            JsonResponse({"version": "1.0"}),
            JsonResponse({"models": [{"name": "qwen2.5:3b"}]}),
            JsonResponse({"message": {"content": "GOTOWE"}}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses):
            result = check_ollama_analysis()
        self.assertEqual(result["analysis"], "gotowa")

    def test_09_context_help_is_available_without_performing_action(self):
        temporary, state = self.make_state(True)
        window = MainWindow(state=state, mail_service=MailService(MinimalMailClient()))
        try:
            with patch.object(QMessageBox, "information") as information:
                window._show_context_help("documents")
            self.assertTrue(information.called)
            self.assertIn("Szukaj w dokumentach", information.call_args.args[2])
        finally:
            self.close_window(window, temporary)

    def test_10_narrow_home_uses_one_column_and_remains_scrollable(self):
        home = HomePage()
        home.resize(520, 520)
        home.show()
        self.app.processEvents()
        positions = [(home.action_grid.getItemPosition(index)[0], home.action_grid.getItemPosition(index)[1]) for index in range(4)]
        self.assertEqual([column for _row, column in positions], [0, 0, 0, 0])
        home.close()
        home.deleteLater()

    def test_11_new_message_opens_editor_but_never_sends_automatically(self):
        temporary, state = self.make_state(True)
        window = MainWindow(state=state, mail_service=MailService(MinimalMailClient()))
        try:
            window._mail_connected = True
            window.account_address = "konto@wp.pl"
            window._start_new_message()
            self.assertIs(window.stack.currentWidget(), window.reply_page)
            self.assertEqual(window.reply_page.page_title.text(), "Nowa wiadomość")
            self.assertFalse(window._reply_running)
            self.assertEqual(window.reply_page.recipient.text(), "")
        finally:
            self.close_window(window, temporary)

    def test_12_dashboard_task_summary_survives_restart_and_respects_progress(self):
        temporary, state = self.make_state(True)
        state.save_dashboard_plan("konto@wp.pl", {
            "message_identity": "plan-1", "mail_uid": "mail-1",
            "tasks": [
                {"key": "krok-a", "action": "Wyślij skan", "deadline": "2026-10-29T16:00"},
                {"key": "krok-b", "action": "Potwierdź udział", "deadline": "2026-10-30T12:00"},
            ],
        })
        state.save_completed_tasks("konto@wp.pl", "plan-1", ["krok-a"])
        window = MainWindow(state=state, mail_service=MailService(MinimalMailClient()))
        try:
            window.account_address = "konto@wp.pl"
            window._mail_connected = True
            window._update_home_summary()
            self.assertIn("1", window.home_page.tasks.text())
            self.assertIn("30.10.2026", window.home_page.deadline.text())
            self.assertIn("Potwierdź udział", window.home_page.last_task.text())
        finally:
            self.close_window(window, temporary)


if __name__ == "__main__":
    unittest.main()
