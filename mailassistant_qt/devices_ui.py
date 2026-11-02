"""Proste okna drukowania, skanowania i ustawień urządzeń."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .devices import device_instructions, validate_pdf
from .state import AppSettings


def _button(text: str, secondary: bool = False) -> QPushButton:
    button = QPushButton(text)
    if secondary:
        button.setObjectName("SecondaryButton")
    return button


class PrintDialog(QDialog):
    """Podgląd PDF i ograniczony, czytelny zestaw ustawień wydruku."""

    def __init__(
        self, path: str, printers: list[dict[str, Any]], default_name: str = "",
        suggested_copies: int = 1, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("Drukowanie dokumentu")
        self.setMinimumSize(540, 420)
        self.resize(980, 720)
        layout = QVBoxLayout(self)
        title = QLabel(f"Dokument: {Path(path).name}")
        title.setObjectName("SectionTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.document = QPdfDocument(self)
        self.document.load(path)
        self.preview = QPdfView()
        self.preview.setDocument(self.document)
        self.preview.setPageMode(QPdfView.PageMode.MultiPage)
        self.preview.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        layout.addWidget(self.preview, 1)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.printer = QComboBox()
        physical = [item for item in printers if not item.get("is_virtual")]
        source = physical or printers
        for item in source:
            status = str(item.get("status") or "nieznany")
            self.printer.addItem(f"{item.get('name')} — {status}", str(item.get("name") or ""))
        selected = self.printer.findData(default_name)
        if selected >= 0:
            self.printer.setCurrentIndex(selected)
        self.copies = QSpinBox()
        self.copies.setRange(1, 99)
        self.copies.setValue(max(1, suggested_copies))
        self.pages = QComboBox()
        self.pages.setEditable(True)
        self.pages.addItem("Wszystkie strony", "")
        self.pages.addItem("Wpisz zakres, np. 1-3,5", "custom")
        self.color = QComboBox()
        self.color.addItem("Kolor", True)
        self.color.addItem("Czarno-biały", False)
        self.paper = QComboBox()
        self.paper.addItems(["A4", "Letter"])
        self.landscape = QCheckBox("Poziomo")
        self.duplex = QCheckBox("Druk dwustronny, jeśli drukarka go obsługuje")
        form.addRow("Drukarka:", self.printer)
        form.addRow("Liczba kopii:", self.copies)
        form.addRow("Strony:", self.pages)
        form.addRow("Tryb:", self.color)
        form.addRow("Papier:", self.paper)
        form.addRow("Ułożenie:", self.landscape)
        form.addRow("Opcja:", self.duplex)
        layout.addLayout(form)
        notice = QLabel("Aplikacja przekaże dokument do kolejki Windows. Po wydruku poprosi o potwierdzenie, czy kartki rzeczywiście wyszły prawidłowo.")
        notice.setObjectName("Muted")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Drukuj")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> dict[str, Any]:
        page_text = self.pages.currentText().strip()
        if self.pages.currentIndex() == 0:
            page_text = ""
        elif "Wpisz zakres" in page_text:
            page_text = ""
        return {
            "printer": self.printer.currentData(), "copies": self.copies.value(),
            "pages": page_text, "color": bool(self.color.currentData()),
            "paper": self.paper.currentText(), "landscape": self.landscape.isChecked(),
            "duplex": self.duplex.isChecked(),
        }

    def accept(self) -> None:
        if not self.printer.currentData():
            QMessageBox.warning(self, "Brak drukarki", "Nie wykryto drukarki, na której można wykonać wydruk.")
            return
        super().accept()

    def done(self, result: int) -> None:
        self.preview.setDocument(None)
        self.document.close()
        self.document.setParent(None)
        self.document.deleteLater()
        super().done(result)


class ScanDialog(QDialog):
    scan_page_requested = Signal(str, str)

    def __init__(self, scanners: list[dict[str, Any]], default_id: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pages: list[str] = []
        self.setWindowTitle("Skanowanie dokumentu")
        self.setMinimumSize(520, 400)
        layout = QVBoxLayout(self)
        heading = QLabel("Skanuj dokument krok po kroku")
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.scanner = QComboBox()
        for item in scanners:
            self.scanner.addItem(str(item.get("name") or "Skaner"), str(item.get("id") or "").removeprefix("scanner:"))
        found = self.scanner.findData(default_id.removeprefix("scanner:"))
        if found >= 0:
            self.scanner.setCurrentIndex(found)
        self.source = QComboBox()
        self.source.addItem("Szyba skanera", "flatbed")
        self.source.addItem("Podajnik dokumentów (ADF)", "adf")
        form.addRow("Skaner:", self.scanner)
        form.addRow("Źródło:", self.source)
        layout.addLayout(form)
        self.hint = QLabel("Połóż pierwszą stronę na szybie, zamknij pokrywę i wybierz „Skanuj stronę”.")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.preview = QLabel("Nie zeskanowano jeszcze żadnej strony.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.preview.setStyleSheet("border: 1px solid #d7e0eb; border-radius: 9px;")
        layout.addWidget(self.preview, 1)
        self.page_list = QListWidget()
        self.page_list.setMaximumHeight(110)
        layout.addWidget(self.page_list)
        actions = QVBoxLayout()
        self.scan_button = _button("Skanuj stronę")
        self.scan_button.clicked.connect(self._scan)
        up = _button("W górę", True)
        down = _button("W dół", True)
        remove = _button("Usuń stronę", True)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        remove.clicked.connect(self._remove)
        actions.addWidget(self.scan_button)
        actions.addWidget(up)
        actions.addWidget(down)
        actions.addWidget(remove)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Zapisz jako PDF")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @Slot()
    def _scan(self) -> None:
        if not self.scanner.currentData():
            QMessageBox.warning(self, "Brak skanera", "Windows nie udostępnia obecnie żadnego skanera WIA.")
            return
        self.set_scanning(True)
        self.scan_page_requested.emit(str(self.scanner.currentData()), str(self.source.currentData()))

    def set_scanning(self, active: bool) -> None:
        self.scan_button.setEnabled(not active)
        self.hint.setText("Trwa pobieranie strony ze skanera…" if active else "Sprawdź podgląd. Możesz dodać kolejną stronę albo zapisać dokument.")

    def add_page(self, path: str) -> None:
        self.pages.append(path)
        self.page_list.addItem(f"Strona {len(self.pages)}")
        self.page_list.setCurrentRow(len(self.pages) - 1)
        pixmap = QPixmap(path)
        self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.set_scanning(False)

    def scan_failed(self, message: str) -> None:
        self.set_scanning(False)
        QMessageBox.warning(self, "Nie udało się zeskanować strony", message)

    def _move(self, offset: int) -> None:
        row = self.page_list.currentRow()
        other = row + offset
        if row < 0 or other < 0 or other >= len(self.pages):
            return
        self.pages[row], self.pages[other] = self.pages[other], self.pages[row]
        item = self.page_list.takeItem(row)
        self.page_list.insertItem(other, item)
        self.page_list.setCurrentRow(other)

    def _remove(self) -> None:
        row = self.page_list.currentRow()
        if row >= 0:
            removed = Path(self.pages.pop(row))
            if removed.name.startswith("mailassistant_scan_"):
                removed.unlink(missing_ok=True)
            self.page_list.takeItem(row)
            for index in range(self.page_list.count()):
                self.page_list.item(index).setText(f"Strona {index + 1}")
            if not self.pages:
                self.preview.setPixmap(QPixmap())
                self.preview.setText("Nie zeskanowano jeszcze żadnej strony.")

    def reject(self) -> None:
        for value in self.pages:
            path = Path(value)
            if path.name.startswith("mailassistant_scan_"):
                path.unlink(missing_ok=True)
        self.pages.clear()
        super().reject()

    def accept(self) -> None:
        if not self.pages:
            QMessageBox.warning(self, "Brak stron", "Najpierw zeskanuj co najmniej jedną stronę.")
            return
        super().accept()


class DevicesPanel(QFrame):
    refresh_requested = Signal()
    print_file_requested = Signal()
    test_print_requested = Signal()
    scan_requested = Signal()
    import_scan_requested = Signal()

    def __init__(self, state: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.state = state
        self.printers: list[dict[str, Any]] = []
        self.scanners: list[dict[str, Any]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        heading = QLabel("Moje urządzenia")
        heading.setObjectName("SectionTitle")
        root.addWidget(heading)
        self.status = QLabel("Wybierz „Odśwież”, aby sprawdzić drukarki i skanery.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        self.list.currentItemChanged.connect(self._selected)
        root.addWidget(self.list)
        defaults = QFormLayout()
        defaults.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.default_printer = QComboBox()
        self.default_scanner = QComboBox()
        self.default_printer.currentIndexChanged.connect(self._save_defaults)
        self.default_scanner.currentIndexChanged.connect(self._save_defaults)
        defaults.addRow("Domyślna drukarka:", self.default_printer)
        defaults.addRow("Domyślny skaner:", self.default_scanner)
        root.addLayout(defaults)
        link_form = QFormLayout()
        link_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.link_printer = QComboBox()
        self.link_scanner = QComboBox()
        link_form.addRow("Funkcja drukowania:", self.link_printer)
        link_form.addRow("Funkcja skanowania:", self.link_scanner)
        root.addLayout(link_form)
        link_actions = QVBoxLayout()
        link_button = _button("Połącz jako jedno urządzenie", True)
        unlink_button = _button("Usuń ręczne połączenie", True)
        link_button.clicked.connect(self._link_devices)
        unlink_button.clicked.connect(self._unlink_devices)
        link_actions.addWidget(link_button)
        link_actions.addWidget(unlink_button)
        root.addLayout(link_actions)
        actions = QVBoxLayout()
        self.refresh_button = _button("Odśwież urządzenia")
        self.refresh_button.clicked.connect(self.refresh_requested)
        print_file = _button("Wydrukuj PDF", True)
        print_file.clicked.connect(self.print_file_requested)
        test_print = _button("Wydrukuj stronę testową", True)
        test_print.clicked.connect(self.test_print_requested)
        scan = _button("Skanuj dokument", True)
        scan.clicked.connect(self.scan_requested)
        import_scan = _button("Wczytaj gotowy skan", True)
        import_scan.clicked.connect(self.import_scan_requested)
        for widget in (self.refresh_button, print_file, test_print, scan, import_scan):
            actions.addWidget(widget)
        root.addLayout(actions)
        self.instructions = QLabel("")
        self.instructions.setWordWrap(True)
        self.instructions.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.instructions)
        self.manual_button = _button("Otwórz stronę pomocy producenta", True)
        self.manual_button.clicked.connect(self._open_manual)
        self.manual_button.hide()
        root.addWidget(self.manual_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._manual_url = ""

    def set_busy(self, busy: bool) -> None:
        self.setEnabled(not busy)
        self.refresh_button.setText("Sprawdzanie urządzeń…" if busy else "Odśwież urządzenia")
        if busy:
            self.status.setText("Sprawdzanie urządzeń i sterowników Windows…")

    def set_devices(self, result: dict[str, Any]) -> None:
        self.setEnabled(True)
        self.printers = list(result.get("printers") or [])
        self.scanners = list(result.get("scanners") or [])
        self.list.clear()
        links = self.state.device_links
        displayed_scanners: set[str] = set()
        display_devices: list[dict[str, Any]] = []
        for printer in self.printers:
            matches = [
                scanner for scanner in self.scanners
                if (printer.get("group_id") and printer.get("group_id") == scanner.get("group_id"))
                or links.get(str(scanner.get("id"))) == str(printer.get("id"))
            ]
            if matches:
                scanner = matches[0]
                displayed_scanners.add(str(scanner.get("id")))
                combined = dict(printer)
                combined["kind"] = "mfp"
                combined["name"] = str(printer.get("name") or scanner.get("name") or "Urządzenie wielofunkcyjne")
                combined["capabilities"] = dict(printer.get("capabilities") or {}) | dict(scanner.get("capabilities") or {})
                combined["status_detail"] = (
                    f"Drukowanie: {printer.get('status', 'nieznany')}; skanowanie: {scanner.get('status', 'nieznany')}."
                )
                display_devices.append(combined)
            else:
                display_devices.append(printer)
        display_devices.extend(scanner for scanner in self.scanners if str(scanner.get("id")) not in displayed_scanners)
        for device in display_devices:
            kind = "Drukarka" if device.get("kind") == "printer" else "Skaner"
            if device.get("kind") == "mfp":
                kind = "Urządzenie wielofunkcyjne"
            virtual = " (wirtualna)" if device.get("is_virtual") else ""
            text = f"{kind}{virtual}: {device.get('name')}\nStan: {device.get('status')} • Połączenie: {device.get('connection')}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, device)
            self.list.addItem(item)
        self.default_printer.blockSignals(True)
        self.default_scanner.blockSignals(True)
        self.default_printer.clear()
        self.default_scanner.clear()
        self.link_printer.clear()
        self.link_scanner.clear()
        for device in self.printers:
            if not device.get("is_virtual"):
                self.default_printer.addItem(str(device.get("name")), str(device.get("name")))
                self.link_printer.addItem(str(device.get("name")), str(device.get("id")))
        for device in self.scanners:
            self.default_scanner.addItem(str(device.get("name")), str(device.get("id")))
            self.link_scanner.addItem(str(device.get("name")), str(device.get("id")))
        self.default_printer.setEnabled(self.default_printer.count() > 0)
        self.default_scanner.setEnabled(self.default_scanner.count() > 0)
        self.link_printer.setEnabled(self.link_printer.count() > 0)
        self.link_scanner.setEnabled(self.link_scanner.count() > 0)
        p_index = self.default_printer.findData(self.state.default_printer)
        s_index = self.default_scanner.findData(self.state.default_scanner)
        if p_index >= 0:
            self.default_printer.setCurrentIndex(p_index)
        if s_index >= 0:
            self.default_scanner.setCurrentIndex(s_index)
        self.default_printer.blockSignals(False)
        self.default_scanner.blockSignals(False)
        self._save_defaults()
        notice = str(result.get("scanner_notice") or "")
        physical = sum(not item.get("is_virtual") for item in self.printers)
        self.status.setText(
            f"Wykryto {physical} fizycznych drukarek i {len(self.scanners)} skanerów."
            + (f"\n{notice}" if notice else "")
        )
        if self.list.count():
            self.list.setCurrentRow(0)

    @Slot()
    def _link_devices(self) -> None:
        printer_id = str(self.link_printer.currentData() or "")
        scanner_id = str(self.link_scanner.currentData() or "")
        if not printer_id or not scanner_id:
            QMessageBox.information(
                self, "Nie można połączyć funkcji",
                "Najpierw Windows musi wykryć zarówno fizyczną drukarkę, jak i skaner.",
            )
            return
        links = self.state.device_links
        links[scanner_id] = printer_id
        self.state.device_links = links
        self.set_devices({"printers": self.printers, "scanners": self.scanners, "scanner_notice": ""})
        self.status.setText("Połączono wybraną drukarkę i skaner jako funkcje jednego urządzenia.")

    @Slot()
    def _unlink_devices(self) -> None:
        scanner_id = str(self.link_scanner.currentData() or "")
        links = self.state.device_links
        if scanner_id in links:
            links.pop(scanner_id, None)
            self.state.device_links = links
            self.set_devices({"printers": self.printers, "scanners": self.scanners, "scanner_notice": ""})
            self.status.setText("Usunięto ręczne połączenie funkcji. Urządzenia znów są pokazane osobno.")

    @Slot()
    def _save_defaults(self) -> None:
        if self.default_printer.currentData():
            self.state.default_printer = str(self.default_printer.currentData())
        if self.default_scanner.currentData():
            self.state.default_scanner = str(self.default_scanner.currentData())

    @Slot(QListWidgetItem, QListWidgetItem)
    def _selected(self, current: QListWidgetItem | None, _old: QListWidgetItem | None) -> None:
        if current is None:
            return
        device = current.data(Qt.ItemDataRole.UserRole) or {}
        guide = device_instructions(str(device.get("name") or ""))
        prefix = "Instrukcja dla tego modelu" if guide.get("exact_model") else "Instrukcja ogólna — model nie został potwierdzony"
        steps = "\n".join(f"{index}. {text}" for index, text in enumerate(guide["steps"], 1))
        capabilities = device.get("capabilities") or {}
        capability_names = {
            "color": "druk kolorowy", "duplex": "druk dwustronny", "flatbed": "szyba skanera",
            "adf": "podajnik dokumentów", "wia": "skanowanie przez Windows WIA",
            "twain": "skanowanie przez TWAIN",
        }
        capability_text = ", ".join(
            capability_names.get(key, key) for key, value in capabilities.items() if value is True
        ) or "sterownik nie podał"
        self.instructions.setText(
            f"{prefix}\nMożliwości: {capability_text}\n\n{steps}\n\n"
            f"Źródło: {guide['source']} • wersja {guide['version']} • data {guide['date']}"
        )
        self._manual_url = str(guide.get("url") or "")
        self.manual_button.setVisible(bool(self._manual_url))

    @Slot()
    def _open_manual(self) -> None:
        if self._manual_url:
            QDesktopServices.openUrl(QUrl(self._manual_url))

