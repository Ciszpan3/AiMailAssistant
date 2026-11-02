import gc
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QPushButton

from mailassistant_qt.devices import (
    Device, device_instructions, images_to_pdf, pair_multifunction_devices,
    parse_page_range, task_device_action, validate_pdf,
    scan_wia_pages,
)
from mailassistant_qt.core import UserFacingError, user_error
from mailassistant_qt.devices_ui import DevicesPanel, PrintDialog, ScanDialog
from mailassistant_qt.main_window import TasksPage
from mailassistant_qt.logic import validate_analysis
from mailassistant_qt.state import AppSettings
from mailassistant_qt.workers import MailService


class AttachmentClient:
    def get_details(self, _uid):
        return "Treść", [{"name": "formularz.pdf", "part": object()}]

    def save_attachment(self, _attachment, path):
        Path(path).write_bytes(b"%PDF-1.4\n%%EOF")

    def disconnect(self):
        pass

    def abort(self):
        pass


class DeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_pdf(self, directory: str, pages: int = 2) -> Path:
        images = []
        for number in range(pages):
            path = Path(directory) / f"page-{number}.png"
            image = QImage(500, 700, QImage.Format.Format_RGB32)
            image.fill(QColor("white" if number == 0 else "lightgray"))
            self.assertTrue(image.save(str(path)))
            images.append(str(path))
        target = Path(directory) / "scan.pdf"
        images_to_pdf(images, str(target))
        return target

    def test_page_range_validation(self):
        self.assertEqual(parse_page_range("", 4), [0, 1, 2, 3])
        self.assertEqual(parse_page_range("1-2,4", 4), [0, 1, 3])
        with self.assertRaisesRegex(RuntimeError, "ma 4 stron"):
            parse_page_range("5", 4)
        with self.assertRaisesRegex(RuntimeError, "np. 1-3,5"):
            parse_page_range("pierwsza", 4)

    def test_scan_images_create_nonempty_valid_pdf(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            target = self.make_pdf(directory, 3)
            verified, count = validate_pdf(target)
            self.assertEqual(verified, target)
            self.assertEqual(count, 3)
            self.assertGreater(target.stat().st_size, 100)

    def test_invalid_and_empty_pdf_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            wrong = Path(directory) / "wrong.pdf"
            wrong.write_bytes(b"not a pdf")
            with self.assertRaisesRegex(RuntimeError, "prawidłowym dokumentem PDF"):
                validate_pdf(wrong)

    def test_task_actions_respect_do_not_print(self):
        self.assertEqual(task_device_action({"action": "Wydrukuj formularz"}), "print")
        self.assertEqual(task_device_action({"action": "Zeskanuj podpisany dokument"}), "scan")
        self.assertEqual(
            task_device_action({"action": "Przeczytaj instrukcję", "instruction": "Nie trzeba jej drukować"}),
            "none",
        )

    def test_mfp_is_not_merged_by_name_only(self):
        printer = Device("p", "Brother DCP-J105", "printer", hardware_id="")
        scanner = Device("s", "Brother DCP-J105", "scanner", hardware_id="")
        pair_multifunction_devices([printer], [scanner])
        self.assertEqual(printer.group_id, "")
        self.assertEqual(scanner.group_id, "")
        printer.hardware_id = r"USB\VID_04F9&PID_02A7"
        scanner.hardware_id = r"USB\VID_04F9&PID_02A7&MI_01"
        pair_multifunction_devices([printer], [scanner])
        self.assertTrue(printer.group_id.startswith("mfp:"))
        self.assertEqual(printer.group_id, scanner.group_id)

    def test_model_instruction_and_generic_fallback_are_labeled(self):
        exact = device_instructions("Brother DCP-J105")
        generic = device_instructions("Nieznany skaner 123")
        self.assertTrue(exact["exact_model"])
        self.assertFalse(generic["exact_model"])
        self.assertIn("Brother", exact["source"])

    def test_device_defaults_persist_without_password_or_documents(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            state = AppSettings(settings)
            panel = DevicesPanel(state)
            panel.set_devices({
                "printers": [{"id": "printer:P", "name": "P", "kind": "printer", "status": "gotowa", "connection": "USB", "is_virtual": False}],
                "scanners": [{"id": "scanner:S", "name": "S", "kind": "scanner", "status": "wykryty", "connection": "WIA"}],
                "scanner_notice": "",
            })
            self.assertEqual(state.default_printer, "P")
            self.assertEqual(state.default_scanner, "scanner:S")
            content = Path(settings.fileName()).read_text(encoding="utf-8")
            self.assertNotIn("password", content.lower())
            panel.deleteLater()

    def test_mail_attachment_can_be_prepared_for_preview(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            service = MailService(AttachmentClient(), cache_root=directory)
            received = []
            service.attachment_prepared.connect(lambda uid, index, path: received.append((uid, index, path)))
            service.prepare_attachment("1", 0)
            self.assertEqual(received[0][:2], ("1", 0))
            self.assertTrue(Path(received[0][2]).read_bytes().startswith(b"%PDF"))
            service.disconnect()

    def test_indexed_server_document_can_be_prepared_after_restart(self):
        class IndexedClient(AttachmentClient):
            def get_indexed_attachment(self, folder, validity, uid, index):
                self.reference = (folder, validity, uid, index)
                return {"name": "Main_CV.pdf", "part": object()}

        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            client = IndexedClient()
            service = MailService(client, cache_root=directory)
            received = []
            service.attachment_prepared.connect(lambda uid, index, path: received.append((uid, index, path)))
            service.prepare_indexed_attachment({
                "folder": "INBOX", "uid_validity": "1792763100", "server_uid": "4",
                "mail_ref": "mail:old-session", "attachment_index": 0,
            })
            self.assertEqual(client.reference, ("INBOX", "1792763100", "4", 0))
            self.assertEqual(received[0][:2], ("mail:old-session", 0))
            self.assertTrue(Path(received[0][2]).is_file())
            service.disconnect()

    def test_manual_mfp_link_shows_one_combined_device(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            state = AppSettings(QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat))
            state.device_links = {"scanner:S": "printer:P"}
            panel = DevicesPanel(state)
            panel.set_devices({
                "printers": [{"id": "printer:P", "name": "Model P", "kind": "printer", "status": "gotowa", "connection": "USB", "is_virtual": False, "capabilities": {"color": True}}],
                "scanners": [{"id": "scanner:S", "name": "Model S", "kind": "scanner", "status": "wykryty", "connection": "WIA", "capabilities": {"flatbed": True}}],
                "scanner_notice": "",
            })
            self.assertEqual(panel.list.count(), 1)
            self.assertIn("Urządzenie wielofunkcyjne", panel.list.item(0).text())
            panel.deleteLater()

    def test_print_dialog_uses_suggested_copy_count(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            pdf = self.make_pdf(directory, 1)
            printers = [{"name": "Drukarka testowa", "status": "gotowa", "is_virtual": False}]
            dialog = PrintDialog(str(pdf), printers, "Drukarka testowa", 2)
            self.assertEqual(dialog.options()["copies"], 2)
            self.assertEqual(dialog.options()["printer"], "Drukarka testowa")
            dialog.reject()
            dialog.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            del dialog
            gc.collect()

    def test_scan_dialog_reorders_and_removes_pages(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            one = Path(directory) / "one.png"
            two = Path(directory) / "two.png"
            image = QImage(20, 20, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            image.save(str(one)); image.save(str(two))
            dialog = ScanDialog([{"id": "scanner:abc", "name": "Skaner"}])
            dialog.add_page(str(one)); dialog.add_page(str(two))
            dialog.page_list.setCurrentRow(1)
            dialog._move(-1)
            self.assertEqual(dialog.pages[0], str(two))
            dialog._remove()
            self.assertEqual(len(dialog.pages), 1)
            dialog.deleteLater()

    def test_tasks_show_device_buttons_but_not_for_no_print_instruction(self):
        page = TasksPage()
        analysis = {
            "summary": "Test", "events": [], "warnings": [],
            "completeness": {"status": "complete", "missing": [], "conflicts": []},
            "tasks": [
                {"id": "1", "action": "Wydrukuj formularz", "deadline": "", "quantity": 2, "condition": "", "instruction": "", "depends_on": [], "confidence": "high", "source_quote": "wydrukować w dwóch egzemplarzach"},
                {"id": "2", "action": "Przeczytaj instrukcję", "deadline": "", "quantity": 0, "condition": "", "instruction": "Nie trzeba jej drukować", "depends_on": [], "confidence": "high", "source_quote": "Nie trzeba jej drukować"},
            ],
        }
        page.set_analysis("x", analysis, attachment_names=["formularz.pdf"])
        labels = [button.text() for button in page.findChildren(QPushButton)]
        self.assertIn("Wydrukuj potrzebny PDF", labels)
        self.assertNotIn("Wydrukuj instrukcję", labels)
        page.deleteLater()

    def test_completeness_does_not_claim_dates_are_missing_when_extracted(self):
        body = (
            "Skan proszę odesłać do 29 października 2026 r. do godziny 16:00. "
            "Spotkanie odbędzie się 3 listopada 2026 r. o godzinie 10:30."
        )
        raw = {
            "summary": "Terminy dokumentów i spotkania.",
            "events": [{
                "id": "e1", "title": "Spotkanie", "start": "2026-11-03T10:30",
                "location": "", "condition": "", "confidence": "high",
                "source_quote": "Spotkanie odbędzie się 3 listopada 2026 r. o godzinie 10:30.",
            }],
            "tasks": [{
                "id": "t1", "action": "Odesłać skan", "deadline": "2026-10-29T16:00",
                "quantity": 0, "condition": "", "instruction": "", "depends_on": [],
                "confidence": "high", "source_quote": "Skan proszę odesłać do 29 października 2026 r. do godziny 16:00.",
            }],
            "warnings": [],
            "completeness": {"status": "incomplete", "missing": ["Data spotkania", "Data odbioru skanu"], "conflicts": []},
        }
        result = validate_analysis(raw, body)
        self.assertEqual(result["completeness"]["missing"], [])
        self.assertEqual(result["completeness"]["status"], "complete")

    def test_device_errors_remain_clear_but_are_explicitly_safe(self):
        error = UserFacingError("Drukarka nie jest dostępna. Odśwież listę urządzeń.")
        self.assertEqual(user_error(error), str(error))

    def test_adf_adapter_collects_pages_until_feeder_is_empty(self):
        class Property:
            Value = 0

        class Properties:
            def Item(self, _key):
                return Property()

        class Image:
            def SaveFile(self, path):
                Path(path).write_bytes(b"fake jpeg page")

        class ScanItem:
            def __init__(self):
                self.count = 0
                self.Properties = Properties()

            def Transfer(self, _format):
                self.count += 1
                if self.count > 2:
                    raise OSError("paper empty")
                return Image()

        scan_item = ScanItem()

        class Items:
            Count = 1

            def Item(self, _index):
                return scan_item

        class DeviceInfo:
            def Connect(self):
                return types.SimpleNamespace(Items=Items())

        class DeviceInfos:
            def Item(self, _device_id):
                return DeviceInfo()

        pythoncom = types.ModuleType("pythoncom")
        pythoncom.CoInitialize = lambda: None
        pythoncom.CoUninitialize = lambda: None
        client = types.ModuleType("win32com.client")
        client.Dispatch = lambda _name: types.SimpleNamespace(DeviceInfos=DeviceInfos())
        package = types.ModuleType("win32com")
        package.client = client
        with patch.dict(sys.modules, {"pythoncom": pythoncom, "win32com": package, "win32com.client": client}):
            pages = scan_wia_pages("device-1", "adf")
        try:
            self.assertEqual(len(pages), 2)
            self.assertTrue(all(Path(path).stat().st_size > 0 for path in pages))
        finally:
            for path in pages:
                Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
