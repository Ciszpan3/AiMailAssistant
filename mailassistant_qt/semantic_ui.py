"""Czytelny ekran wyszukiwania wiadomości po swobodnym opisie."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)



class SemanticResultCard(QFrame):
    open_requested = Signal(object)

    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self.record = dict(record)
        self.setObjectName("Card")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 13, 16, 13)
        root.setSpacing(6)
        top = QHBoxLayout()
        label = QLabel(str(record.get("label") or "Może pasować"))
        label.setObjectName("SectionTitle")
        top.addWidget(label)
        top.addStretch(1)
        date = QLabel(str(record.get("date") or "Data nieznana"))
        date.setObjectName("Muted")
        top.addWidget(date)
        root.addLayout(top)
        subject = QLabel(str(record.get("subject") or "(brak tematu)"))
        subject.setObjectName("SectionTitle")
        subject.setWordWrap(True)
        root.addWidget(subject)
        sender = QLabel(f"Od: {record.get('sender') or '(brak nadawcy)'}")
        sender.setObjectName("Muted")
        sender.setWordWrap(True)
        root.addWidget(sender)
        # Najważniejsza czynność pozostaje widoczna nawet wtedy, gdy długi
        # cytat wymaga przewinięcia karty na niskim ekranie.
        button = QPushButton("Otwórz wiadomość")
        button.setMinimumHeight(44)
        button.clicked.connect(lambda: self.open_requested.emit(self.record))
        root.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
        explanation = QLabel(str(record.get("explanation") or "Pasujący fragment wiadomości:"))
        explanation.setWordWrap(True)
        root.addWidget(explanation)
        quote = QLabel(f"„{record.get('snippet') or 'Brak czytelnego fragmentu.'}”")
        quote.setObjectName("Muted")
        quote.setWordWrap(True)
        quote.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(quote)


class SemanticSearchPage(QWidget):
    back_requested = Signal()
    search_requested = Signal(str, str)
    prepare_requested = Signal()
    cancel_requested = Signal()
    rebuild_requested = Signal()
    open_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_result_rows)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 24)
        root.setSpacing(12)
        header = QHBoxLayout()
        back = QPushButton("← Wróć do poczty")
        back.setObjectName("SecondaryButton")
        back.clicked.connect(self.back_requested)
        header.addWidget(back)
        header.addStretch(1)
        self.rebuild_button = QPushButton("Odbuduj wyszukiwanie")
        self.rebuild_button.setObjectName("QuietButton")
        self.rebuild_button.setToolTip("Usuwa tylko lokalny indeks wyszukiwania i tworzy go ponownie. Nie usuwa maili.")
        self.rebuild_button.clicked.connect(self.rebuild_requested)
        header.addWidget(self.rebuild_button)
        root.addLayout(header)

        title = QLabel("Szukaj po opisie wiadomości")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        intro = QLabel(
            "Nie musisz pamiętać tematu, daty ani dokładnych słów. Napisz własnymi słowami, czego dotyczył mail."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.query = QTextEdit()
        self.query.setAccessibleName("Opis szukanej wiadomości")
        self.query.setPlaceholderText(
            "Opisz własnymi słowami, czego dotyczyła wiadomość, której szukasz..."
        )
        self.query.setMinimumHeight(82)
        self.query.setMaximumHeight(120)
        root.addWidget(self.query)
        examples = QLabel(
            "Przykłady: „Ktoś pisał o projekcie na studia” • „Proszono mnie o dokument” • "
            "„Ktoś zapraszał mnie na spotkanie” • „Informacja o jakiejś płatności”"
        )
        examples.setObjectName("Muted")
        examples.setWordWrap(True)
        root.addWidget(examples)

        controls = QGridLayout()
        self.folder = QComboBox()
        self.folder.setAccessibleName("Foldery objęte wyszukiwaniem")
        self.folder.addItem("Wszystkie przygotowane foldery", "")
        controls.addWidget(self.folder, 0, 0, 1, 2)
        self.search_button = QPushButton("Znajdź wiadomość")
        self.search_button.clicked.connect(self._search)
        controls.addWidget(self.search_button, 1, 0)
        self.prepare_button = QPushButton("Dodaj starsze wiadomości do wyszukiwania")
        self.prepare_button.setObjectName("SecondaryButton")
        self.prepare_button.clicked.connect(self.prepare_requested)
        self.prepare_button.setToolTip("Aplikacja przejrzy starsze maile i doda je do lokalnego wyszukiwania. Wiadomości pozostaną na serwerze.")
        controls.addWidget(self.prepare_button, 1, 1)
        self.cancel_button = QPushButton("Anuluj")
        self.cancel_button.setObjectName("SecondaryButton")
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.hide()
        controls.addWidget(self.cancel_button, 2, 0, 1, 2)
        controls.setColumnStretch(0, 1)
        controls.setColumnStretch(1, 1)
        root.addLayout(controls)

        self.status = QLabel("Wyszukiwanie po opisie przygotowuje się stopniowo podczas korzystania z poczty.")
        self.status.setObjectName("StatusInfo")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.results = QListWidget()
        self.results.setObjectName("SemanticResults")
        self.results.setSpacing(8)
        self.results.setMinimumHeight(250)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.results.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        root.addWidget(self.results, 1)
        QWidget.setTabOrder(self.query, self.folder)
        QWidget.setTabOrder(self.folder, self.search_button)
        QWidget.setTabOrder(self.search_button, self.prepare_button)
        QWidget.setTabOrder(self.prepare_button, self.results)

    def set_folders(self, folders: list[dict[str, Any]]) -> None:
        selected = str(self.folder.currentData() or "")
        self.folder.clear()
        self.folder.addItem("Wszystkie przygotowane foldery", "")
        for folder in folders:
            if folder.get("role") == "trash":
                continue
            identifier = str(folder.get("id") or "")
            if identifier:
                self.folder.addItem(str(folder.get("name") or identifier), identifier)
        index = self.folder.findData(selected)
        if index >= 0:
            self.folder.setCurrentIndex(index)

    @Slot()
    def _search(self) -> None:
        self.search_requested.emit(self.query.toPlainText().strip(), str(self.folder.currentData() or ""))

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.search_button.setEnabled(not busy)
        self.query.setEnabled(not busy)
        self.folder.setEnabled(not busy)
        self.search_button.setText("Szukanie pasujących wiadomości…" if busy else "Znajdź wiadomość")
        if text:
            self.status.setText(text)

    def set_indexing(self, active: bool, text: str = "") -> None:
        self._indexing_active = bool(active)
        self.prepare_button.setVisible(not active)
        self.cancel_button.setVisible(active)
        self.rebuild_button.setEnabled(not active)
        if text:
            self.status.setText(text)

    def show_results(self, value: object) -> None:
        payload = value if isinstance(value, dict) else {}
        records = payload.get("results") if isinstance(payload.get("results"), list) else []
        self.results.clear()
        for record in records:
            if not isinstance(record, dict):
                continue
            widget = SemanticResultCard(record)
            widget.open_requested.connect(self.open_requested)
            item = QListWidgetItem()
            item.setSizeHint(QSize(480, max(210, widget.sizeHint().height())))
            self.results.addItem(item)
            self.results.setItemWidget(item, widget)
        self._fit_timer.start(0)
        indexed = int(payload.get("indexed") or 0)
        complete = bool(payload.get("complete"))
        semantic_available = bool(payload.get("semantic_available", True))
        if not records:
            text = (
                "Nie znaleziono wystarczająco pasującej wiadomości. "
                "Spróbuj dodać nazwisko, temat albo sytuację."
            )
        elif semantic_available:
            text = f"Pokazano {len(records)} najbardziej pasujących wiadomości."
        else:
            text = "Model znaczeniowy jest niedostępny. Pokazano wyłącznie mniej elastyczne wyniki tekstowe."
        if not complete:
            if getattr(self, "_indexing_active", False):
                text += " Trwa przygotowywanie starszych wiadomości. Wyniki mogą być jeszcze niepełne."
            else:
                text += (
                    " Starsze wiadomości nie są jeszcze przygotowane. "
                    "Wybierz „Dodaj starsze wiadomości do wyszukiwania”, aby objąć nimi wyniki."
                )
        if indexed:
            text += f" Przygotowane wiadomości: {indexed}."
        error = str(payload.get("semantic_error") or "").strip()
        if error:
            text += f" {error}"
        verification_notice = str(payload.get("verification_notice") or "").strip()
        if verification_notice and records:
            text += " Część niejednoznacznych wyników została pominięta, ponieważ nie udało się ich dokładnie potwierdzić."
        self.status.setText(text)

    def _fit_result_rows(self) -> None:
        """Dopasowuje każdą kartę po ustaleniu rzeczywistej szerokości listy."""
        width = max(260, self.results.viewport().width() - 14)
        for index in range(self.results.count()):
            item = self.results.item(index)
            widget = self.results.itemWidget(item)
            if widget is None or widget.layout() is None:
                continue
            widget.setFixedWidth(width)
            widget.layout().invalidate()
            measured = widget.layout().heightForWidth(width)
            if measured < 0:
                measured = widget.sizeHint().height()
            item.setSizeHint(QSize(width, max(210, measured + 10)))

    def resizeEvent(self, event: QResizeEvent) -> None:
        # Na niskim ekranie pole opisu pozostaje wygodne, ale nie zabiera
        # miejsca wynikom i przyciskowi otwarcia wiadomości.
        self.query.setMaximumHeight(104 if event.size().height() < 850 else 120)
        self._fit_timer.start(0)
        super().resizeEvent(event)

    def reset(self) -> None:
        self.results.clear()
        self.query.clear()
        self.set_busy(False)
        self.set_indexing(False)
        self.status.setText("Zaloguj się, aby wyszukiwać wiadomości po opisie.")
