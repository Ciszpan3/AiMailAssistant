"""Czytelna biblioteka dokumentów i lokalny podgląd PDF."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from .documents import CATEGORIES


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setMinimumWidth(0)
    frame.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(9)
    return frame, layout


class DocumentListRow(QWidget):
    """Karta dokumentu z jasną nazwą, pochodzeniem i datą."""

    def __init__(self, name: str, source: str, availability: str, details: str, favorite: bool) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(5)
        title = QLabel(("★ " if favorite else "") + name)
        title.setFont(QFont("Segoe UI", -1, QFont.Weight.DemiBold))
        title.setWordWrap(True)
        root.addWidget(title)
        origin = QLabel(source)
        origin.setObjectName("Badge")
        origin.setWordWrap(True)
        root.addWidget(origin, 0, Qt.AlignmentFlag.AlignLeft)
        availability_label = QLabel(availability)
        availability_label.setObjectName("Muted")
        availability_label.setWordWrap(True)
        root.addWidget(availability_label)
        detail = QLabel(details)
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        root.addWidget(detail)
        self.setAccessibleName(f"{name}. {source}. {availability}. {details}")


class DocumentPreviewPage(QWidget):
    """Osobny, przestronny ekran podglądu jednego dokumentu PDF."""

    back_requested = Signal()
    print_requested = Signal(object)
    save_copy_requested = Signal(object)
    message_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.current_record: dict[str, Any] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 20, 26, 24)
        root.setSpacing(12)

        self.back_button = QPushButton("← Wróć do dokumentów")
        self.back_button.setObjectName("SecondaryButton")
        self.back_button.clicked.connect(self.back_requested)
        root.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.title = QLabel("Podgląd dokumentu")
        self.title.setObjectName("PageTitle")
        self.title.setWordWrap(True)
        root.addWidget(self.title)

        self.status = QLabel("Dokument zostanie pokazany poniżej.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        actions = QGridLayout()
        self.print_button = QPushButton("Drukuj")
        self.print_button.setObjectName("SecondaryButton")
        self.print_button.clicked.connect(lambda: self.print_requested.emit(dict(self.current_record)))
        self.save_button = QPushButton("Zapisz kopię")
        self.save_button.setObjectName("SecondaryButton")
        self.save_button.clicked.connect(lambda: self.save_copy_requested.emit(dict(self.current_record)))
        self.message_button = QPushButton("Otwórz wiadomość")
        self.message_button.setObjectName("QuietButton")
        self.message_button.clicked.connect(lambda: self.message_requested.emit(dict(self.current_record)))
        actions.addWidget(self.print_button, 0, 0)
        actions.addWidget(self.save_button, 0, 1)
        actions.addWidget(self.message_button, 1, 0, 1, 2)
        root.addLayout(actions)

        self.pdf_document = QPdfDocument(self)
        self.pdf_document.pageCountChanged.connect(self._page_count_changed)
        self.pdf_buffer: QBuffer | None = None
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        root.addWidget(self.pdf_view, 1)

    def show_pdf(self, path: str, name: str = "Dokument PDF", record: dict[str, Any] | None = None) -> bool:
        self.close_pdf()
        self.current_record = dict(record or {})
        self.message_button.setEnabled(bool(self.current_record.get("mail_ref")))
        self.pdf_view.setDocument(self.pdf_document)
        self.title.setText(name or Path(path).name or "Dokument PDF")
        try:
            data = Path(path).read_bytes()
        except OSError:
            self.status.setText("Nie można odczytać pobranego pliku PDF.")
            self.pdf_view.hide()
            return False
        if not data or len(data) > 100 * 1024 * 1024:
            self.status.setText("Dokument PDF jest pusty albo przekracza limit 100 MB.")
            self.pdf_view.hide()
            return False
        # Ładujemy z pamięci, aby Windows nie blokował tymczasowego pliku
        # podczas powrotu do biblioteki lub zamykania aplikacji.
        self.pdf_buffer = QBuffer(self)
        self.pdf_buffer.setData(QByteArray(data))
        self.pdf_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        error = self.pdf_document.load(self.pdf_buffer)
        # Przeciążenie load(QIODevice) w PySide6 zwraca None i raportuje
        # ewentualny błąd przez QPdfDocument.error().
        if error is not None and error != QPdfDocument.Error.None_:
            self.status.setText(
                "Nie można wyświetlić tego PDF-u. Plik może być uszkodzony albo zabezpieczony hasłem."
            )
            self.pdf_view.hide()
            return False
        count = self.pdf_document.pageCount()
        self.status.setText(
            f"Dokument ma {count} stron. Przewijaj, aby zobaczyć kolejne."
            if count > 0 else "Ładowanie stron dokumentu…"
        )
        self.pdf_view.show()
        return True

    @Slot(int)
    def _page_count_changed(self, count: int) -> None:
        if count > 0:
            self.status.setText(f"Dokument ma {count} stron. Przewijaj, aby zobaczyć kolejne.")

    def close_pdf(self) -> None:
        self.pdf_view.setDocument(None)
        self.pdf_document.close()
        if self.pdf_buffer is not None:
            self.pdf_buffer.close()
            self.pdf_buffer.deleteLater()
            self.pdf_buffer = None

    def reset(self) -> None:
        self.close_pdf()
        self.title.setText("Podgląd dokumentu")
        self.status.setText("Najpierw zaloguj się i wybierz dokument.")
        self.current_record = {}
        self.message_button.setEnabled(False)
        self.pdf_view.hide()

    def closeEvent(self, event) -> None:
        self.pdf_view.setDocument(None)
        self.pdf_document.close()
        super().closeEvent(event)


class DocumentsPage(QWidget):
    search_requested = Signal(str, str, int, int, bool)
    server_preview_requested = Signal(object)
    print_requested = Signal(object)
    save_copy_requested = Signal(object)
    message_requested = Signal(object)
    task_requested = Signal(object)
    favorite_requested = Signal(str)
    rebuild_requested = Signal()
    ocr_requested = Signal(object)
    help_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, dict[str, Any]] = {}
        self.current: dict[str, Any] | None = None
        self._compact = False
        self.offset = 0
        self.total = 0
        self.page_limit = 25
        self._page_action = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(14)
        heading = QHBoxLayout()
        title = QLabel("Moje dokumenty")
        title.setObjectName("PageTitle")
        heading.addWidget(title, 1)
        help_button = QPushButton("Potrzebuję pomocy")
        help_button.setObjectName("QuietButton")
        help_button.clicked.connect(self.help_requested)
        heading.addWidget(help_button)
        root.addLayout(heading)
        self.status = QLabel("Biblioteka pokazuje rzeczywiste załączniki, skany i dokumenty wysłane.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        search = QVBoxLayout()
        self.query = QLineEdit()
        self.query.setAccessibleName("Szukaj w dokumentach")
        self.query.setPlaceholderText("Wpisz nazwę pliku, nadawcę, datę albo słowo z dokumentu")
        self.query.returnPressed.connect(self._search)
        search.addWidget(self.query)
        search_hint = QLabel(
            "Przykład: formularz, Kowalska, 27.10.2026 albo podpisany skan. "
            "Aplikacja może najpierw przygotować same nazwy załączników z całej poczty — bez pobierania plików PDF."
        )
        search_hint.setObjectName("Muted")
        search_hint.setWordWrap(True)
        search.addWidget(search_hint)
        self.whole_mailbox = QCheckBox("Szukaj w całej skrzynce pocztowej")
        self.whole_mailbox.setChecked(True)
        self.whole_mailbox.setToolTip(
            "Obejmuje również starsze wiadomości i inne foldery, a nie tylko dokumenty z aktualnie oglądanej strony."
        )
        search.addWidget(self.whole_mailbox)
        actions = QHBoxLayout()
        self.search_button = QPushButton("Szukaj w dokumentach")
        self.search_button.clicked.connect(self._search)
        self.rebuild_button = QPushButton("Sprawdź listę dokumentów")
        self.rebuild_button.setObjectName("SecondaryButton")
        self.rebuild_button.clicked.connect(self.rebuild_requested)
        actions.addWidget(self.search_button)
        actions.addWidget(self.rebuild_button)
        actions.addStretch(1)
        search.addLayout(actions)
        root.addLayout(search)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)
        categories, category_layout = _card()
        self.category_card = categories
        label = QLabel("Kategorie")
        label.setObjectName("SectionTitle")
        category_layout.addWidget(label)
        self.category_list = QListWidget()
        self.category_list.setAccessibleName("Kategorie dokumentów")
        for key, value in CATEGORIES.items():
            item = QListWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.category_list.addItem(item)
        self.category_list.setCurrentRow(0)
        self.category_list.currentItemChanged.connect(lambda _a, _b: self._search())
        category_layout.addWidget(self.category_list, 1)

        results, results_layout = _card()
        self.results_card = results
        label = QLabel("Dokumenty")
        label.setObjectName("SectionTitle")
        results_layout.addWidget(label)
        self.result_list = QListWidget()
        self.result_list.setAccessibleName("Lista dokumentów")
        self.result_list.setWordWrap(True)
        self.result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.result_list.currentItemChanged.connect(self._selected)
        results_layout.addWidget(self.result_list, 1)
        self.pagination_bar = QWidget()
        pagination = QHBoxLayout(self.pagination_bar)
        pagination.setContentsMargins(0, 3, 0, 0)
        pagination.setSpacing(8)
        self.newer_button = QPushButton("← Nowsze")
        self.newer_button.setObjectName("SecondaryButton")
        self.newer_button.clicked.connect(self._newer_page)
        pagination.addWidget(self.newer_button)
        self.pagination_label = QLabel("Zakres dokumentów pojawi się po wyszukaniu.")
        self.pagination_label.setObjectName("Muted")
        self.pagination_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pagination_label.setWordWrap(True)
        pagination.addWidget(self.pagination_label, 1)
        self.older_button = QPushButton("Starsze →")
        self.older_button.setObjectName("SecondaryButton")
        self.older_button.clicked.connect(self._older_page)
        pagination.addWidget(self.older_button)
        results_layout.addWidget(self.pagination_bar)
        page_size_row = QHBoxLayout()
        page_size_row.addWidget(QLabel("Na jednej stronie:"))
        self.page_size = QComboBox()
        for size in (25, 50, 100):
            self.page_size.addItem(f"{size} dokumentów", size)
        self.page_size.currentIndexChanged.connect(self._page_size_changed)
        page_size_row.addWidget(self.page_size)
        page_size_row.addStretch(1)
        results_layout.addLayout(page_size_row)

        preview, preview_layout = _card()
        self.preview_card = preview
        self.back_button = QPushButton("Wróć do dokumentów")
        self.back_button.setObjectName("SecondaryButton")
        self.back_button.clicked.connect(self._show_list)
        self.back_button.hide()
        preview_layout.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.document_title = QLabel("Wybierz dokument")
        self.document_title.setObjectName("SectionTitle")
        self.document_title.setWordWrap(True)
        preview_layout.addWidget(self.document_title)
        self.metadata = QLabel("")
        self.metadata.setWordWrap(True)
        self.metadata.setObjectName("Muted")
        preview_layout.addWidget(self.metadata)
        self.preview_notice = QLabel("Podgląd pojawi się po wybraniu dokumentu.")
        self.preview_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_notice.setWordWrap(True)
        preview_layout.addWidget(self.preview_notice)
        buttons = QVBoxLayout()
        self.open_button = QPushButton("Otwórz podgląd PDF")
        self.open_button.clicked.connect(self._open)
        self.print_button = QPushButton("Drukuj")
        self.print_button.setObjectName("SecondaryButton")
        self.print_button.clicked.connect(lambda: self._emit_record(self.print_requested))
        self.save_button = QPushButton("Zapisz kopię")
        self.save_button.setObjectName("SecondaryButton")
        self.save_button.clicked.connect(lambda: self._emit_record(self.save_copy_requested))
        self.message_button = QPushButton("Pokaż powiązaną wiadomość")
        self.message_button.setObjectName("QuietButton")
        self.message_button.clicked.connect(lambda: self._emit_record(self.message_requested))
        self.task_button = QPushButton("Pokaż powiązane zadanie")
        self.task_button.setObjectName("QuietButton")
        self.task_button.clicked.connect(lambda: self._emit_record(self.task_requested))
        self.favorite_button = QPushButton("Dodaj do ulubionych")
        self.favorite_button.setObjectName("QuietButton")
        self.favorite_button.clicked.connect(self._favorite)
        self.ocr_button = QPushButton("Dodaj treść dokumentu do wyszukiwania")
        self.ocr_button.setObjectName("QuietButton")
        self.ocr_button.setToolTip(
            "Aplikacja odczyta zwykły tekst PDF. Dla skanu bez tekstu użyje lokalnego programu OCR, jeśli jest zainstalowany."
        )
        self.ocr_button.clicked.connect(lambda: self._emit_record(self.ocr_requested))
        for button in (self.open_button, self.print_button, self.save_button, self.message_button,
                       self.task_button, self.favorite_button, self.ocr_button):
            buttons.addWidget(button)
        preview_layout.addLayout(buttons)

        self.splitter.addWidget(categories)
        self.splitter.addWidget(results)
        self.splitter.addWidget(preview)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 4)
        self.splitter.setStretchFactor(2, 7)
        root.addWidget(self.splitter, 1)
        self._set_buttons(False)

    def category(self) -> str:
        item = self.category_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else "all"

    @Slot()
    def _search(self) -> None:
        self.offset = 0
        self._request_page(0, "search")

    def set_rows(self, values: list[dict[str, Any]]) -> None:
        self.set_page({
            "rows": values, "total": len(values), "offset": 0,
            "limit": max(1, len(values) or self.page_limit),
        })

    def set_page(self, value: object) -> None:
        payload = value if isinstance(value, dict) else {}
        values = [dict(row) for row in payload.get("rows", []) if isinstance(row, dict)]
        self.offset = max(0, int(payload.get("offset") or 0))
        self.total = max(0, int(payload.get("total") or len(values)))
        self.page_limit = max(1, min(100, int(payload.get("limit") or self.page_limit)))
        self.rows = {str(row.get("id") or ""): dict(row) for row in values}
        self.result_list.clear()
        for row in values:
            identifier = str(row.get("id") or "")
            category_labels = {
                "received": "otrzymany", "scanned": "zeskanowany",
                "sent": "wysłany", "to_sign": "do podpisania",
            }
            categories = [
                value for value in str(row.get("source_categories") or row.get("category") or "").split(",")
                if value in category_labels
            ]
            source = " i ".join(category_labels[value] for value in categories) or "dokument"
            availability = "na komputerze" if row.get("local_path") else "na serwerze poczty"
            links = int(row.get("source_count") or 1)
            link_text = f" • {links} powiązania" if links > 1 else ""
            task = f"\nZadanie: {row.get('task_labels')}" if row.get("task_labels") else ""
            star = "★ " if row.get("favorite") else ""
            accessible_text = (
                f"{star}{row.get('name', 'Dokument.pdf')}\n{source} • {availability}{link_text}\n"
                f"{row.get('document_date') or row.get('subject') or 'Data nieznana'}{task}"
            )
            # Widoczny tekst należy do DocumentListRow. Pusty tekst elementu
            # zapobiega nałożeniu starego renderowania na nową kartę.
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, identifier)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
            item.setToolTip(str(row.get("name") or ""))
            details = str(row.get("document_date") or row.get("subject") or "Data nieznana")
            if row.get("task_labels"):
                details += f"\nZadanie: {row.get('task_labels')}"
            widget = DocumentListRow(
                str(row.get("name") or "Dokument.pdf"),
                source + (f" • {links} powiązania" if links > 1 else ""),
                availability,
                details,
                bool(row.get("favorite")),
            )
            item.setSizeHint(QSize(300, max(126, widget.sizeHint().height())))
            self.result_list.addItem(item)
            self.result_list.setItemWidget(item, widget)
        QTimer.singleShot(0, self._fit_document_rows)
        start = self.offset + 1 if values else 0
        end = self.offset + len(values)
        pages = max(1, (self.total + self.page_limit - 1) // self.page_limit)
        current_page = min(pages, self.offset // self.page_limit + 1)
        self.pagination_label.setText(
            f"Dokumenty {start}–{end} z {self.total}  •  strona {current_page} z {pages}"
            if self.total else "Brak dokumentów."
        )
        self.newer_button.setEnabled(self.offset > 0)
        self.older_button.setEnabled(self.offset + len(values) < self.total)
        combo_index = self.page_size.findData(self.page_limit)
        if combo_index >= 0 and combo_index != self.page_size.currentIndex():
            self.page_size.blockSignals(True)
            self.page_size.setCurrentIndex(combo_index)
            self.page_size.blockSignals(False)
        self.status.setText(f"Znaleziono {self.total} unikalnych dokumentów.")
        if values:
            self.result_list.setCurrentRow(0)
        else:
            self.current = None
            self._set_buttons(False)
            self.document_title.setText("Nie znaleziono dokumentów")
            self.metadata.setText("Zmień kategorię albo wpisz inne słowa.")
            self.preview_notice.setText("Wybierz dokument z listy.")
            self.preview_notice.show()

    def _fit_document_rows(self) -> None:
        """Przelicza wysokość kart, gdy tekst zawija się w wąskiej liście."""
        width = max(80, self.result_list.viewport().width() - 12)
        for index in range(self.result_list.count()):
            item = self.result_list.item(index)
            widget = self.result_list.itemWidget(item)
            if widget is None or widget.layout() is None:
                continue
            widget.setFixedWidth(width)
            widget.layout().invalidate()
            measured = widget.layout().heightForWidth(width)
            if measured < 0:
                measured = widget.sizeHint().height()
            item.setSizeHint(QSize(width, max(126, measured + 8)))

    def reset(self) -> None:
        """Usuwa z widoku dane poprzedniej, nieuwierzytelnionej sesji."""
        self.rows.clear()
        self.current = None
        self.result_list.clear()
        self.query.clear()
        self.offset = 0
        self.total = 0
        self._page_action = ""
        self.pagination_label.setText("Zakres dokumentów pojawi się po wyszukaniu.")
        self.newer_button.setEnabled(False)
        self.older_button.setEnabled(False)
        self.category_list.setCurrentRow(0)
        self.document_title.setText("Zaloguj się, aby zobaczyć dokumenty")
        self.metadata.clear()
        self.preview_notice.setText("Dokumenty pojawią się po połączeniu z pocztą.")
        self.preview_notice.show()
        self.status.setText("Konto nie jest połączone.")
        self._set_buttons(False)

    @Slot(QListWidgetItem, QListWidgetItem)
    def _selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        self.current = self.rows.get(str(current.data(Qt.ItemDataRole.UserRole)))
        if not self.current:
            return
        row = self.current
        self.document_title.setText(str(row.get("name") or "Dokument PDF"))
        size = int(row.get("size") or 0)
        source = "Plik lokalny" if row.get("local_path") else "Dokument dostępny na serwerze poczty"
        details = source + (f"\nRozmiar: {size / 1024:.0f} KB" if size else "")
        if int(row.get("source_count") or 1) > 1:
            details += f"\nTen dokument ma {row.get('source_count')} powiązania z pocztą."
        details += f"\nWiadomość: {row.get('subject')}" if row.get("subject") else ""
        details += f"\nPowiązane zadania: {row.get('task_labels')}" if row.get("task_labels") else ""
        self.metadata.setText(details)
        self.favorite_button.setText("Usuń z ulubionych" if row.get("favorite") else "Dodaj do ulubionych")
        self._set_buttons(True)
        local = Path(str(row.get("local_path") or ""))
        if local.is_file():
            self.preview_notice.setText(
                "Dokument jest zapisany na komputerze. Wybierz „Otwórz podgląd PDF”, aby zobaczyć go na osobnym ekranie."
            )
            self.preview_notice.show()
        else:
            self.preview_notice.setText("Dokument pozostaje na serwerze. Wybierz „Otwórz podgląd PDF”, aby pobrać go tymczasowo.")
            self.preview_notice.show()
        if self._compact:
            self.category_card.hide()
            self.results_card.hide()
            self.preview_card.show()
            self.back_button.show()

    def _set_buttons(self, enabled: bool) -> None:
        for button in (self.open_button, self.print_button, self.save_button, self.message_button,
                       self.task_button, self.favorite_button, self.ocr_button):
            button.setEnabled(enabled)

    def set_document_busy(self, busy: bool, text: str = "") -> None:
        self.open_button.setText("Pobieranie PDF…" if busy else "Otwórz podgląd PDF")
        for button in (self.open_button, self.print_button, self.save_button, self.ocr_button):
            button.setEnabled(not busy and self.current is not None)
        if text:
            self.status.setText(text)
            self.preview_notice.setText(text)
            self.preview_notice.show()

    def set_search_busy(self, busy: bool, text: str = "") -> None:
        self.search_button.setEnabled(not busy)
        self.rebuild_button.setEnabled(not busy)
        self.query.setEnabled(not busy)
        self.whole_mailbox.setEnabled(not busy)
        self.category_list.setEnabled(not busy)
        self.page_size.setEnabled(not busy)
        self.newer_button.setEnabled(not busy and self.offset > 0)
        self.older_button.setEnabled(not busy and self.offset + self.result_list.count() < self.total)
        self.search_button.setText("Wyszukiwanie…" if busy else "Szukaj w dokumentach")
        self.newer_button.setText(
            "Pobieranie nowszych…" if busy and self._page_action == "newer" else "← Nowsze"
        )
        self.older_button.setText(
            "Pobieranie starszych…" if busy and self._page_action == "older" else "Starsze →"
        )
        if text:
            self.status.setText(text)
        if not busy:
            self._page_action = ""

    def _request_page(self, offset: int, action: str) -> None:
        if not self.search_button.isEnabled() and action != "search":
            return
        self._page_action = action
        self.set_search_busy(True, "Wyszukiwanie dokumentów…")
        self.search_requested.emit(
            self.query.text().strip(), self.category(), max(0, int(offset)),
            self.page_limit, self.whole_mailbox.isChecked(),
        )

    @Slot()
    def _older_page(self) -> None:
        next_offset = self.offset + self.page_limit
        if next_offset < self.total:
            self._request_page(next_offset, "older")

    @Slot()
    def _newer_page(self) -> None:
        if self.offset > 0:
            self._request_page(max(0, self.offset - self.page_limit), "newer")

    @Slot(int)
    def _page_size_changed(self, index: int) -> None:
        if index < 0:
            return
        try:
            selected = int(self.page_size.itemData(index))
        except (TypeError, ValueError):
            return
        if selected == self.page_limit:
            return
        self.page_limit = selected
        self._request_page(0, "search")

    def set_content_busy(self, busy: bool) -> None:
        self.ocr_button.setEnabled(not busy and self.current is not None)
        self.ocr_button.setText(
            "Odczytywanie treści…" if busy else "Dodaj treść dokumentu do wyszukiwania"
        )

    @Slot()
    def _open(self) -> None:
        if not self.current:
            return
        self.server_preview_requested.emit(dict(self.current))

    def _emit_record(self, signal: Any) -> None:
        if self.current:
            signal.emit(dict(self.current))

    @Slot()
    def _favorite(self) -> None:
        if self.current:
            self.favorite_requested.emit(str(self.current.get("id") or ""))

    @Slot()
    def _show_list(self) -> None:
        self.preview_card.hide()
        self.category_card.show()
        self.results_card.show()

    def resizeEvent(self, event) -> None:
        self._compact = event.size().width() < 900
        self.splitter.setOrientation(Qt.Orientation.Vertical if self._compact else Qt.Orientation.Horizontal)
        self.category_card.setMaximumHeight(180 if self._compact else 16777215)
        self.results_card.setMaximumHeight(330 if self._compact else 16777215)
        if not self._compact:
            self.category_card.show()
            self.results_card.show()
            self.preview_card.show()
        self.back_button.setVisible(self._compact and self.preview_card.isVisible())
        QTimer.singleShot(0, self._fit_document_rows)
        super().resizeEvent(event)

