"""Prosty ekran startowy i widoki prowadzące dla mniej doświadczonego użytkownika."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(12)
    return frame, layout


class HomePage(QWidget):
    mail_requested = Signal()
    tasks_requested = Signal()
    documents_requested = Signal()
    compose_requested = Signal()
    continue_requested = Signal()
    help_requested = Signal()
    settings_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._compact = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 30)
        root.setSpacing(18)

        heading = QHBoxLayout()
        title = QLabel("Co chcesz zrobić?")
        title.setObjectName("HeroTitle")
        title.setWordWrap(True)
        heading.addWidget(title, 1)
        help_button = QPushButton("Potrzebuję pomocy")
        help_button.setObjectName("QuietButton")
        help_button.clicked.connect(self.help_requested)
        heading.addWidget(help_button)
        settings_button = QPushButton("Ustawienia")
        settings_button.setObjectName("QuietButton")
        settings_button.clicked.connect(self.settings_requested)
        heading.addWidget(settings_button)
        root.addLayout(heading)

        intro = QLabel("Wybierz jedną z czterech najważniejszych czynności.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.action_grid = QGridLayout()
        self.action_grid.setSpacing(16)
        self.mail_button = self._action(
            "Sprawdź pocztę", "Otwórz odebrane wiadomości", self.mail_requested, QStyle.StandardPixmap.SP_ArrowDown
        )
        self.tasks_button = self._action(
            "Moje zadania", "Zobacz, co trzeba wykonać", self.tasks_requested, QStyle.StandardPixmap.SP_DialogApplyButton
        )
        self.documents_button = self._action(
            "Znajdź dokument", "Odszukaj PDF albo skan", self.documents_requested, QStyle.StandardPixmap.SP_FileIcon
        )
        self.compose_button = self._action(
            "Napisz wiadomość", "Przygotuj nową wiadomość", self.compose_requested, QStyle.StandardPixmap.SP_FileDialogNewFolder
        )
        self.action_buttons = [self.mail_button, self.tasks_button, self.documents_button, self.compose_button]
        self._arrange_actions(False)
        root.addLayout(self.action_grid)

        self.continue_button = QPushButton("Kontynuuj ostatnie zadanie")
        self.continue_button.setObjectName("SeniorContinue")
        self.continue_button.clicked.connect(self.continue_requested)
        self.continue_button.hide()
        root.addWidget(self.continue_button)

        status_card, status_layout = _card()
        status_title = QLabel("Najważniejsze informacje")
        status_title.setObjectName("SectionTitle")
        status_layout.addWidget(status_title)
        self.connection = QLabel("Poczta: konto nie jest połączone")
        self.unread = QLabel("Nowe wiadomości: zaloguj się, aby sprawdzić")
        self.tasks = QLabel("Zadania do wykonania: brak otwartego planu")
        self.deadline = QLabel("Najbliższy termin: nie znaleziono")
        self.last_task = QLabel("Ostatnie zadanie: brak")
        for label in (self.connection, self.unread, self.tasks, self.deadline, self.last_task):
            label.setWordWrap(True)
            status_layout.addWidget(label)
        root.addWidget(status_card)
        root.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    @staticmethod
    def _action(title: str, description: str, signal: Any, icon: QStyle.StandardPixmap) -> QPushButton:
        button = QPushButton(f"{title}\n{description}")
        button.setObjectName("SeniorAction")
        button.setMinimumHeight(112)
        app = QApplication.instance()
        if app is not None:
            button.setIcon(app.style().standardIcon(icon))
            button.setIconSize(QSize(34, 34))
        button.setAccessibleName(title)
        button.setAccessibleDescription(description)
        button.clicked.connect(signal)
        return button

    def _arrange_actions(self, compact: bool) -> None:
        for button in self.action_buttons if hasattr(self, "action_buttons") else []:
            self.action_grid.removeWidget(button)
        buttons = getattr(self, "action_buttons", [])
        columns = 1 if compact else 2
        for index, button in enumerate(buttons):
            self.action_grid.addWidget(button, index // columns, index % columns)

    def set_summary(
        self,
        connected: bool,
        unread: int | None,
        remaining_tasks: int,
        nearest_deadline: str,
        last_task: str,
    ) -> None:
        self.connection.setText("Poczta: połączona" if connected else "Poczta: konto nie jest połączone")
        if connected and unread is not None:
            self.unread.setText(f"Nowe wiadomości na pobranej liście: {unread}")
        else:
            self.unread.setText("Nowe wiadomości: zaloguj się, aby sprawdzić")
        self.tasks.setText(f"Zadania do wykonania: {remaining_tasks}" if remaining_tasks else "Zadania do wykonania: brak")
        self.deadline.setText(f"Najbliższy termin: {nearest_deadline or 'nie znaleziono'}")
        self.last_task.setText(f"Ostatnie zadanie: {last_task or 'brak'}")
        self.continue_button.setVisible(remaining_tasks > 0)

    def resizeEvent(self, event) -> None:
        compact = event.size().width() < 760
        if compact != self._compact:
            self._compact = compact
            self._arrange_actions(compact)
        super().resizeEvent(event)


class HelpPage(QWidget):
    """Stały, prosty ekran pomocy zamiast technicznego podręcznika."""

    mail_requested = Signal()
    tasks_requested = Signal()
    documents_requested = Signal()
    search_requested = Signal()
    settings_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 30)
        root.setSpacing(16)

        eyebrow = QLabel("POMOC")
        eyebrow.setObjectName("Eyebrow")
        root.addWidget(eyebrow)
        title = QLabel("Jak możemy Ci pomóc?")
        title.setObjectName("PageTitle")
        title.setWordWrap(True)
        root.addWidget(title)
        intro = QLabel(
            "Wybierz czynność. Aplikacja przeniesie Cię do właściwego miejsca, "
            "ale niczego nie wyśle ani nie wydrukuje bez Twojego potwierdzenia."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        actions, actions_layout = _card()
        heading = QLabel("Najczęstsze czynności")
        heading.setObjectName("SectionTitle")
        actions_layout.addWidget(heading)
        for text, description, signal in (
            ("Otwórz pocztę", "Przeczytaj odebrane i wysłane wiadomości.", self.mail_requested),
            ("Zobacz moje zadania", "Wróć do planu przygotowanego z wiadomości.", self.tasks_requested),
            ("Znajdź dokument", "Odszukaj załącznik, skan albo zapisany PDF.", self.documents_requested),
            ("Znajdź wiadomość po opisie", "Opisz własnymi słowami, czego dotyczył mail.", self.search_requested),
        ):
            button = QPushButton(text)
            button.setObjectName("SecondaryButton")
            button.setToolTip(description)
            button.setAccessibleDescription(description)
            button.clicked.connect(signal)
            actions_layout.addWidget(button)
        root.addWidget(actions)

        safety, safety_layout = _card()
        safety_title = QLabel("Bezpieczne korzystanie")
        safety_title.setObjectName("SectionTitle")
        safety_layout.addWidget(safety_title)
        safety_text = QLabel(
            "• Hasło do poczty nie jest zapisywane.\n"
            "• Analiza wiadomości działa lokalnie na tym komputerze.\n"
            "• Przed wysłaniem zawsze zobaczysz pełną treść, odbiorcę i załączniki.\n"
            "• Drukowanie, skanowanie i usuwanie wymagają wyraźnej decyzji."
        )
        safety_text.setWordWrap(True)
        safety_layout.addWidget(safety_text)
        root.addWidget(safety)

        settings = QPushButton("Otwórz ustawienia i diagnostykę")
        settings.setObjectName("QuietButton")
        settings.clicked.connect(self.settings_requested)
        root.addWidget(settings, 0, Qt.AlignmentFlag.AlignLeft)
        root.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)


class FirstRunWizard(QWidget):
    mail_check_requested = Signal(str, str)
    ai_check_requested = Signal()
    devices_check_requested = Signal()
    finished_requested = Signal(dict)

    STEP_TITLES = (
        "Witaj",
        "Konto pocztowe",
        "Asystent AI",
        "Drukarka i skaner",
        "Wygląd",
        "Wszystko gotowe",
    )

    def __init__(self) -> None:
        super().__init__()
        self.mail_ok = False
        self.ai_ok = False
        self.devices_checked = False
        self._devices: dict[str, Any] = {"printers": [], "scanners": []}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 22)
        outer.setSpacing(14)
        self.progress = QLabel("")
        self.progress.setObjectName("Muted")
        outer.addWidget(self.progress)
        self.title = QLabel("")
        self.title.setObjectName("PageTitle")
        self.title.setWordWrap(True)
        outer.addWidget(self.title)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome_page())
        self.stack.addWidget(self._mail_page())
        self.stack.addWidget(self._ai_page())
        self.stack.addWidget(self._devices_page())
        self.stack.addWidget(self._appearance_page())
        self.stack.addWidget(self._finish_page())
        outer.addWidget(self.stack, 1)
        navigation = QHBoxLayout()
        self.back_button = QPushButton("Wstecz")
        self.back_button.setObjectName("SecondaryButton")
        self.back_button.clicked.connect(self.previous_step)
        self.next_button = QPushButton("Rozpocznij")
        self.next_button.clicked.connect(self.next_step)
        navigation.addWidget(self.back_button)
        navigation.addStretch(1)
        navigation.addWidget(self.next_button)
        outer.addLayout(navigation)
        self._update_step()

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(14)
        page.setWidget(content)
        return page, layout

    @staticmethod
    def _plain(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _welcome_page(self) -> QWidget:
        page, layout = self._page()
        panel, content = _card()
        content.addWidget(self._plain(
            "Witaj w Asystencie Poczty. Pomożemy Ci odbierać wiadomości, korzystać z dokumentów "
            "i wykonywać zadania krok po kroku."
        ))
        note = self._plain("Konfiguracja ma sześć krótkich kroków. W każdej chwili możesz wrócić przyciskiem „Wstecz”.")
        note.setObjectName("Muted")
        content.addWidget(note)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def _mail_page(self) -> QWidget:
        page, layout = self._page()
        panel, content = _card()
        content.addWidget(self._plain("Wpisz dane konta WP Poczta. Hasło będzie używane tylko do działania aplikacji i nie zostanie zapisane."))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.mail_address = QLineEdit()
        self.mail_address.setAccessibleName("Adres e-mail konta WP")
        self.mail_address.setPlaceholderText("np. imie.nazwisko@wp.pl")
        self.mail_password = QLineEdit()
        self.mail_password.setAccessibleName("Hasło do poczty")
        self.mail_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.mail_password.setPlaceholderText("Hasło do poczty")
        form.addRow("Adres e-mail:", self.mail_address)
        form.addRow("Hasło:", self.mail_password)
        content.addLayout(form)
        self.mail_check_button = QPushButton("Sprawdź połączenie z pocztą")
        self.mail_check_button.clicked.connect(self._check_mail)
        content.addWidget(self.mail_check_button)
        self.mail_status = self._plain("Połączenie nie zostało jeszcze sprawdzone.")
        self.mail_status.setObjectName("Muted")
        content.addWidget(self.mail_status)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def _ai_page(self) -> QWidget:
        page, layout = self._page()
        panel, content = _card()
        content.addWidget(self._plain(
            "Sprawdzimy lokalnego Asystenta AI oraz model qwen2.5:3b. Żadna wiadomość nie zostanie wysłana do internetu."
        ))
        self.ai_check_button = QPushButton("Sprawdź Asystenta AI")
        self.ai_check_button.clicked.connect(self._check_ai)
        content.addWidget(self.ai_check_button)
        self.ai_status = self._plain("Asystent AI nie został jeszcze sprawdzony.")
        self.ai_status.setObjectName("Muted")
        content.addWidget(self.ai_status)
        self.ai_instruction = self._plain(
            "Jeśli Asystent AI nie działa: uruchom program Ollama, a następnie model poleceniem „ollama run qwen2.5:3b”. "
            "Jeśli Ollamy nie ma na komputerze, zainstaluj ją samodzielnie z oficjalnej strony. Aplikacja niczego nie pobierze bez Twojej zgody."
        )
        self.ai_instruction.setObjectName("Muted")
        self.ai_instruction.hide()
        content.addWidget(self.ai_instruction)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def _devices_page(self) -> QWidget:
        page, layout = self._page()
        panel, content = _card()
        content.addWidget(self._plain(
            "Sprawdzimy urządzenia dostępne w Windows. Test nie uruchomi drukowania ani skanowania."
        ))
        self.devices_check_button = QPushButton("Wykryj drukarki i skanery")
        self.devices_check_button.clicked.connect(self._check_devices)
        content.addWidget(self.devices_check_button)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.printer_combo = QComboBox()
        self.scanner_combo = QComboBox()
        form.addRow("Domyślna drukarka:", self.printer_combo)
        form.addRow("Domyślny skaner:", self.scanner_combo)
        content.addLayout(form)
        self.devices_status = self._plain("Urządzenia nie zostały jeszcze sprawdzone.")
        self.devices_status.setObjectName("Muted")
        content.addWidget(self.devices_status)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def _appearance_page(self) -> QWidget:
        page, layout = self._page()
        panel, content = _card()
        content.addWidget(self._plain("Wybierz ustawienia, które są dla Ciebie najczytelniejsze."))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.text_size = QComboBox()
        self.text_size.addItem("Standardowy tekst", 100)
        self.text_size.addItem("Duży tekst", 120)
        self.text_size.setCurrentIndex(1)
        self.contrast = QCheckBox("Zwiększony kontrast")
        self.mode = QComboBox()
        self.mode.addItem("Tryb prosty", True)
        self.mode.addItem("Tryb zaawansowany", False)
        form.addRow("Rozmiar tekstu:", self.text_size)
        form.addRow("Wygląd:", self.contrast)
        form.addRow("Sposób obsługi:", self.mode)
        content.addLayout(form)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def _finish_page(self) -> QWidget:
        page, layout = self._page()
        panel, content = _card()
        message = self._plain("Wszystko gotowe. Możesz teraz korzystać z Asystenta Poczty.")
        message.setObjectName("SectionTitle")
        content.addWidget(message)
        self.finish_summary = self._plain("")
        content.addWidget(self.finish_summary)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def reset(self, address: str, font_scale: int, high_contrast: bool, simple_mode: bool) -> None:
        self.mail_ok = False
        self.ai_ok = False
        self.devices_checked = False
        self.mail_address.setText(address)
        self.mail_password.clear()
        self.mail_status.setText("Połączenie nie zostało jeszcze sprawdzone.")
        self.ai_status.setText("Asystent AI nie został jeszcze sprawdzony.")
        self.ai_instruction.hide()
        self.devices_status.setText("Urządzenia nie zostały jeszcze sprawdzone.")
        self.text_size.setCurrentIndex(max(0, self.text_size.findData(120 if font_scale >= 120 else 100)))
        self.contrast.setChecked(high_contrast)
        self.mode.setCurrentIndex(max(0, self.mode.findData(simple_mode)))
        self.stack.setCurrentIndex(0)
        self._update_step()

    @Slot()
    def _check_mail(self) -> None:
        address = self.mail_address.text().strip()
        password = self.mail_password.text()
        if not address or not password:
            self.set_mail_result(False, "Wpisz adres e-mail i hasło.")
            return
        self.mail_check_button.setEnabled(False)
        self.mail_check_button.setText("Sprawdzanie połączenia…")
        self.mail_status.setText("Łączenie z WP Pocztą…")
        self.mail_check_requested.emit(address, password)

    def set_mail_result(self, success: bool, message: str) -> None:
        self.mail_ok = success
        self.mail_password.clear()
        self.mail_check_button.setEnabled(True)
        self.mail_check_button.setText("Sprawdź ponownie" if success else "Sprawdź połączenie z pocztą")
        self.mail_status.setText(message)

    @Slot()
    def _check_ai(self) -> None:
        self.ai_check_button.setEnabled(False)
        self.ai_check_button.setText("Sprawdzanie Asystenta AI…")
        self.ai_status.setText("Trwa krótka analiza testowa…")
        self.ai_check_requested.emit()

    def set_ai_result(self, success: bool, message: str) -> None:
        self.ai_ok = success
        self.ai_check_button.setEnabled(True)
        self.ai_check_button.setText("Sprawdź ponownie" if success else "Sprawdź Asystenta AI")
        self.ai_status.setText(message)
        self.ai_instruction.setVisible(not success)

    @Slot()
    def _check_devices(self) -> None:
        self.devices_check_button.setEnabled(False)
        self.devices_check_button.setText("Wykrywanie urządzeń…")
        self.devices_status.setText("Windows sprawdza dostępne drukarki i skanery…")
        self.devices_check_requested.emit()

    def set_devices(self, result: dict[str, Any], default_printer: str = "", default_scanner: str = "") -> None:
        self._devices = dict(result)
        printers = [item for item in result.get("printers", []) if isinstance(item, dict)]
        scanners = [item for item in result.get("scanners", []) if isinstance(item, dict)]
        self.printer_combo.clear()
        self.scanner_combo.clear()
        self.printer_combo.addItem("Nie wybrano", "")
        self.scanner_combo.addItem("Nie wybrano", "")
        for item in printers:
            self.printer_combo.addItem(str(item.get("name") or "Drukarka"), str(item.get("name") or ""))
        for item in scanners:
            self.scanner_combo.addItem(str(item.get("name") or "Skaner"), str(item.get("id") or ""))
        p_index = self.printer_combo.findData(default_printer)
        s_index = self.scanner_combo.findData(default_scanner)
        if p_index >= 0:
            self.printer_combo.setCurrentIndex(p_index)
        if s_index >= 0:
            self.scanner_combo.setCurrentIndex(s_index)
        self.devices_checked = True
        self.devices_check_button.setEnabled(True)
        self.devices_check_button.setText("Sprawdź ponownie")
        if printers or scanners:
            self.devices_status.setText(f"Znaleziono drukarki: {len(printers)}. Znaleziono skanery: {len(scanners)}.")
        else:
            self.devices_status.setText("Nie znaleziono urządzeń. Sprawdź, czy są włączone i widoczne w Windows.")

    def set_devices_error(self, message: str) -> None:
        self.devices_checked = True
        self.devices_check_button.setEnabled(True)
        self.devices_check_button.setText("Spróbuj ponownie")
        self.devices_status.setText(message)

    @Slot()
    def previous_step(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._update_step()

    @Slot()
    def next_step(self) -> None:
        index = self.stack.currentIndex()
        if index < self.stack.count() - 1:
            self.stack.setCurrentIndex(index + 1)
            self._update_step()
            return
        self.finished_requested.emit({
            "font_scale": int(self.text_size.currentData() or 120),
            "high_contrast": self.contrast.isChecked(),
            "simple_mode": bool(self.mode.currentData()),
            "default_printer": str(self.printer_combo.currentData() or ""),
            "default_scanner": str(self.scanner_combo.currentData() or ""),
            "mail_ok": self.mail_ok,
            "ai_ok": self.ai_ok,
            "devices_checked": self.devices_checked,
        })

    def _update_step(self) -> None:
        index = self.stack.currentIndex()
        self.progress.setText(f"Krok {index + 1} z {self.stack.count()}")
        self.title.setText(self.STEP_TITLES[index])
        self.back_button.setEnabled(index > 0)
        if index == 0:
            self.next_button.setText("Rozpocznij")
        elif index == self.stack.count() - 1:
            self.next_button.setText("Przejdź do poczty")
            rows = [
                "✓ Poczta działa" if self.mail_ok else "• Poczta wymaga połączenia",
                "✓ Asystent AI działa" if self.ai_ok else "• Asystent AI wymaga konfiguracji",
                "✓ Urządzenia zostały sprawdzone" if self.devices_checked else "• Urządzenia nie zostały sprawdzone",
            ]
            self.finish_summary.setText("\n".join(rows))
        else:
            self.next_button.setText("Dalej")
