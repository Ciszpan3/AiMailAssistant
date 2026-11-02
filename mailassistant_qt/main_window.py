"""Główne okno i widoki Asystenta Poczty w PySide6."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any
import shutil
from datetime import datetime

from PySide6.QtCore import QSize, QTimer, Qt, QThread, QThreadPool, Signal, Slot
from PySide6.QtGui import QCloseEvent, QFont, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QDialog,
)

from .core import MODEL_NAME, OLLAMA_URL, format_polish_datetime
from .backup import BackupManager, default_backup_directory
from .devices import create_test_pdf, discover_devices, images_to_pdf, print_pdf, scan_wia_pages, task_device_action, validate_pdf
from .devices_ui import DevicesPanel, PrintDialog, ScanDialog
from .documents import DocumentIndex, extract_pdf_text, recognize_document_text
from .documents_ui import DocumentPreviewPage, DocumentsPage
from .reply import (
    DraftStore, ensure_message_id, generate_reply, preflight_warnings,
    send_reply_safe, validate_attachments,
)
from .reply_ui import ReplyPage, ReviewDialog, confirm_delete_draft
from .senior_ui import FirstRunWizard, HelpPage, HomePage
from .semantic_search import (
    EMBEDDING_MODEL, OllamaEmbedder, OllamaSearchVerifier, SemanticMailIndex,
)
from .semantic_ui import SemanticSearchPage
from .state import AppSettings, stable_task_key
from .styles import ensure_readable_font, stylesheet
from .workers import MailService, check_ollama, check_ollama_analysis, create_analysis_worker, FunctionWorker


def clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            clear_layout(child_layout)  # type: ignore[arg-type]


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(9)
    return frame, layout


def friendly_error(area: str, message: str) -> str:
    """Ukrywa techniczny żargon, pozostawiając pełny opis w diagnostyce."""
    text = " ".join(str(message or "").split())
    technical = any(word in text.casefold() for word in (
        "traceback", "exception", "errno", "socket", "imap", "smtp", "python",
        "jsondecode", "attributeerror", "runtimeerror", "winerror",
    ))
    if text and not technical and len(text) <= 280:
        return text
    messages = {
        "poczta": "Nie udało się połączyć z pocztą. Sprawdź internet, adres i hasło, a następnie spróbuj ponownie.",
        "folder poczty": "Nie udało się teraz pobrać tego folderu. Sprawdź połączenie i spróbuj ponownie.",
        "załącznik": "Nie udało się pobrać dokumentu. Wiadomość nie została zmieniona; możesz spróbować ponownie.",
        "folder Wysłane": "Nie udało się teraz sprawdzić folderu Wysłane.",
    }
    return messages.get(area, "Nie udało się wykonać tej czynności. Spróbuj ponownie za chwilę.")


class MailListRow(QWidget):
    """Zwarty, jednorzędowy wpis poczty podobny do typowych klientów e-mail."""

    def __init__(self, sender: str, subject: str, date: str, snippet: str, badges: list[str]) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMinimumWidth(0)
        self._sender = str(sender or "(brak adresu)")
        self._subject = str(subject or "(brak tematu)")
        self._snippet = str(snippet or "")
        self._badges = list(badges)
        unread = "Nieprzeczytana" in badges

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 4, 12, 4)
        root.setSpacing(10)

        self.sender_label = QLabel(self._sender)
        self.sender_label.setMinimumWidth(0)
        self.sender_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if unread:
            self.sender_label.setFont(QFont("Segoe UI", -1, QFont.Weight.Bold))
        root.addWidget(self.sender_label, 23)

        self.subject_label = QLabel(self._subject)
        self.subject_label.setMinimumWidth(0)
        self.subject_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.subject_label.setFont(QFont("Segoe UI", -1, QFont.Weight.Bold if unread else QFont.Weight.DemiBold))
        root.addWidget(self.subject_label, 30)

        self.separator = QLabel("•")
        self.separator.setObjectName("Muted")
        self.separator.setVisible(bool(self._snippet))
        root.addWidget(self.separator)

        self.snippet_label = QLabel(self._snippet)
        self.snippet_label.setObjectName("Muted")
        self.snippet_label.setMinimumWidth(0)
        self.snippet_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.snippet_label.setVisible(bool(self._snippet))
        root.addWidget(self.snippet_label, 42)

        clear_statuses = []
        if "Nieprzeczytana" in badges:
            clear_statuses.append("Nowa")
        if "Załącznik PDF" in badges:
            clear_statuses.append("Załącznik PDF")
        self.badge_label = QLabel(" · ".join(clear_statuses))
        self.badge_label.setObjectName("MailStatus")
        self.badge_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.badge_label.setToolTip(" • ".join(badges))
        self.badge_label.setVisible(bool(clear_statuses))

        self.date_label = QLabel(date)
        self.date_label.setObjectName("Muted")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        date_width = self.date_label.fontMetrics().horizontalAdvance(date) + 10
        status_width = self.badge_label.fontMetrics().horizontalAdvance(self.badge_label.text()) + 10
        self._date_only_width = min(190, max(118, date_width))
        self._date_width = min(330, max(self._date_only_width, date_width + status_width + 14))
        self.status_panel = QWidget()
        self.status_panel.setObjectName("MailStatusPanel")
        status_layout = QHBoxLayout(self.status_panel)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)
        status_layout.addStretch(1)
        status_layout.addWidget(self.badge_label)
        status_layout.addWidget(self.date_label)
        self.status_panel.setMinimumWidth(self._date_width)
        root.addWidget(self.status_panel)
        self.setMinimumHeight(54)
        self.setAccessibleName(f"{sender}. {subject}. {date}")

    @staticmethod
    def _elide(label: QLabel, value: str) -> None:
        available = max(20, label.width() - 4)
        shown = label.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, available)
        label.setText(shown)
        label.setToolTip(value if shown != value else "")

    def resizeEvent(self, event: QResizeEvent) -> None:
        width = event.size().width()
        narrow = width < 690
        very_narrow = width < 520
        self.snippet_label.setVisible(bool(self._snippet) and not narrow)
        self.separator.setVisible(bool(self._snippet) and not narrow)
        self.badge_label.setVisible(bool(self._badges) and not very_narrow)
        target_width = self._date_only_width if very_narrow else min(self._date_width, 235) if narrow else self._date_width
        self.status_panel.setMinimumWidth(target_width)
        super().resizeEvent(event)
        self._refresh_elision()

    def _refresh_elision(self) -> None:
        self._elide(self.sender_label, self._sender)
        self._elide(self.subject_label, self._subject)
        if self.snippet_label.isVisible():
            self._elide(self.snippet_label, self._snippet)


class MailTreeWidget(QTreeWidget):
    """Natywna lista kolumnowa z interfejsem zgodnym z dotychczasową listą."""

    def count(self) -> int:
        return self.topLevelItemCount()

    def item(self, index: int) -> QTreeWidgetItem | None:
        return self.topLevelItem(index)

    def addItem(self, item: QTreeWidgetItem) -> None:
        self.addTopLevelItem(item)


class MailPage(QWidget):
    message_selected = Signal(str)
    mailbox_requested = Signal(str, int, int, str)
    refresh_requested = Signal(str, int, str)
    help_requested = Signal()
    semantic_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, dict[str, Any]] = {}
        self.current_uid = ""
        self.attachments_by_uid: dict[str, list[str]] = {}
        self.bodies_by_uid: dict[str, str] = {}
        self.all_rows: list[dict[str, Any]] = []
        self.folders: dict[str, dict[str, Any]] = {}
        self.current_folder = "INBOX"
        self.view_folder = "INBOX"
        self.offset = 0
        self.total = 0
        self.page_limit = 50
        self._page_action = ""
        self._compact = False
        self._filters_expanded = False

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(7)
        header = QGridLayout()
        title = QLabel("Poczta")
        title.setObjectName("PageTitle")
        header.addWidget(title, 0, 0)
        help_button = QPushButton("Potrzebuję pomocy")
        self.help_button = help_button
        help_button.setObjectName("QuietButton")
        help_button.clicked.connect(self.help_requested)
        header.addWidget(help_button, 0, 3, Qt.AlignmentFlag.AlignRight)
        self.refresh_button = QPushButton("Odśwież pocztę")
        self.refresh_button.clicked.connect(self._refresh_messages)
        self.semantic_button = QPushButton("Szukaj po opisie")
        self.semantic_button.setObjectName("SecondaryButton")
        self.semantic_button.clicked.connect(self.semantic_requested)
        header.addWidget(self.refresh_button, 0, 1)
        header.addWidget(self.semantic_button, 0, 2)
        header.setColumnStretch(0, 1)
        root.addLayout(header)
        self.status = QLabel("Połącz konto w Ustawieniach, aby zobaczyć wiadomości.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        search_row = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName("Szukaj wiadomości")
        self.search.setPlaceholderText("Szukaj wiadomości: nadawca, odbiorca, temat albo data")
        self.search.returnPressed.connect(self._search_messages)
        self.search_scope = QComboBox()
        self.search_scope.setAccessibleName("Zakres wyszukiwania wiadomości")
        self.search_scope.addItem("Tylko w otwartym folderze", "current")
        self.search_scope.addItem("W całej skrzynce pocztowej", "all")
        self.search_scope.setCurrentIndex(self.search_scope.findData("all"))
        self.search_button = QPushButton("Szukaj")
        self.search_button.clicked.connect(self._search_messages)
        self.clear_search_button = QPushButton("Wyczyść")
        self.clear_search_button.setObjectName("SecondaryButton")
        self.clear_search_button.clicked.connect(self._clear_search)
        search_row.addWidget(self.search)
        search_options = QHBoxLayout()
        search_options.addWidget(self.search_scope)
        search_options.addWidget(self.search_button)
        search_options.addWidget(self.clear_search_button)
        search_options.addStretch(1)
        search_row.addLayout(search_options)
        search_hint = QLabel(
            "Wyszukiwanie obejmuje wiadomości na serwerze, a nie tylko pozycje widoczne na bieżącej stronie."
        )
        search_hint.setObjectName("Muted")
        search_hint.setWordWrap(True)
        search_row.addWidget(search_hint)
        root.addLayout(search_row)

        self.filter_panel = QWidget()
        filter_row = QVBoxLayout(self.filter_panel)
        filter_row.setContentsMargins(0, 0, 0, 0)
        self.message_filter = QComboBox()
        self.message_filter.setAccessibleName("Filtr listy wiadomości")
        self.message_filter.addItem("Wszystkie wiadomości", "all")
        self.message_filter.addItem("Tylko nieprzeczytane", "unread")
        self.message_filter.addItem("Tylko z załącznikiem", "attachments")
        self.message_filter.currentIndexChanged.connect(self._apply_view)
        self.message_sort = QComboBox()
        self.message_sort.setAccessibleName("Kolejność wiadomości")
        self.message_sort.addItem("Najnowsze najpierw", "newest")
        self.message_sort.addItem("Najstarsze najpierw", "oldest")
        self.message_sort.addItem("Temat A–Z", "subject")
        self.message_sort.currentIndexChanged.connect(self._apply_view)
        self.page_size = QComboBox()
        self.page_size.setAccessibleName("Liczba wiadomości na stronie")
        for size in (25, 50, 100):
            self.page_size.addItem(f"{size} wiadomości", size)
        self.page_size.setCurrentIndex(self.page_size.findData(self.page_limit))
        self.page_size.currentIndexChanged.connect(self._page_size_changed)
        show_row = QHBoxLayout()
        show_row.addWidget(QLabel("Pokaż:"))
        show_row.addWidget(self.message_filter)
        show_row.addStretch(1)
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Sortuj:"))
        sort_row.addWidget(self.message_sort)
        sort_row.addStretch(1)
        page_row = QHBoxLayout()
        page_row.addWidget(QLabel("Na jednej stronie:"))
        page_row.addWidget(self.page_size)
        page_row.addStretch(1)
        filter_row.addLayout(show_row)
        filter_row.addLayout(sort_row)
        filter_row.addLayout(page_row)
        root.addWidget(self.filter_panel)
        self.filter_toggle = QToolButton()
        self.filter_toggle.setText("Filtry i sortowanie")
        self.filter_toggle.clicked.connect(self._toggle_filters)
        self.filter_panel.hide()
        root.addWidget(self.filter_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.compact_folder_row = QWidget()
        compact_folder_layout = QHBoxLayout(self.compact_folder_row)
        compact_folder_layout.setContentsMargins(0, 0, 0, 0)
        compact_folder_layout.addWidget(QLabel("Folder:"))
        self.folder_combo = QComboBox()
        self.folder_combo.setAccessibleName("Folder pocztowy")
        self.folder_combo.currentIndexChanged.connect(self._folder_combo_changed)
        compact_folder_layout.addWidget(self.folder_combo, 1)
        self.compact_folder_row.hide()
        root.addWidget(self.compact_folder_row)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setMinimumWidth(0)
        folders, folders_layout = card()
        self.folder_card = folders
        folders.setMinimumWidth(150)
        folders.setMaximumWidth(220)
        folders_layout.setContentsMargins(12, 12, 12, 12)
        folders_layout.addWidget(self._section("Foldery pocztowe"))
        self.folder_list = QListWidget()
        self.folder_list.currentItemChanged.connect(self._folder_changed)
        folders_layout.addWidget(self.folder_list, 1)
        local_drafts = QLabel("Lokalne szkice odpowiedzi są przechowywane osobno i pojawiają się w zakładce Odpowiedź.")
        local_drafts.setObjectName("Muted")
        local_drafts.setWordWrap(True)
        folders_layout.addWidget(local_drafts)

        left, left_layout = card()
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(5)
        left_layout.addWidget(self._section("Wiadomości"))
        self.message_list = MailTreeWidget()
        self.message_list.setObjectName("MessageList")
        self.message_list.setColumnCount(4)
        self.message_list.setHeaderHidden(True)
        self.message_list.setRootIsDecorated(False)
        self.message_list.setIndentation(0)
        self.message_list.setUniformRowHeights(True)
        self.message_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.message_list.setWordWrap(False)
        self.message_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.message_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.message_list.itemClicked.connect(lambda item, _column: self._open_message_item(item))
        self.message_list.itemActivated.connect(lambda item, _column: self._open_message_item(item))
        left_layout.addWidget(self.message_list, 1)
        self.pagination_bar = QWidget()
        pagination = QHBoxLayout(self.pagination_bar)
        pagination.setContentsMargins(0, 3, 0, 0)
        pagination.setSpacing(8)
        self.newer_button = QPushButton("← Nowsze")
        self.newer_button.setObjectName("SecondaryButton")
        self.newer_button.setAccessibleName("Pokaż nowsze wiadomości")
        self.newer_button.setToolTip("Przejdź do poprzedniej strony, czyli do nowszych wiadomości.")
        self.newer_button.clicked.connect(self._newer_page)
        pagination.addWidget(self.newer_button)
        self.pagination_label = QLabel("Zakres wiadomości pojawi się po pobraniu poczty.")
        self.pagination_label.setObjectName("Muted")
        self.pagination_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pagination_label.setWordWrap(True)
        pagination.addWidget(self.pagination_label, 1)
        self.older_button = QPushButton("Starsze →")
        self.older_button.setObjectName("SecondaryButton")
        self.older_button.setAccessibleName("Pokaż starsze wiadomości")
        self.older_button.setToolTip("Przejdź do następnej strony, czyli do starszych wiadomości.")
        self.older_button.clicked.connect(self._older_page)
        pagination.addWidget(self.older_button)
        self.load_more_button = self.older_button  # zgodność ze starszym kodem i testami
        self.pagination_bar.hide()
        left_layout.addWidget(self.pagination_bar)

        self.mail_list_card = left
        for panel in (folders, left):
            panel.setMinimumWidth(0)
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.splitter.addWidget(folders)
        self.splitter.addWidget(left)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 8)
        self.splitter.setSizes([180, 1000])
        root.addWidget(self.splitter, 1)
        QWidget.setTabOrder(self.search, self.search_scope)
        QWidget.setTabOrder(self.search_scope, self.search_button)
        QWidget.setTabOrder(self.search_button, self.clear_search_button)
        QWidget.setTabOrder(self.clear_search_button, self.message_filter)
        QWidget.setTabOrder(self.message_filter, self.message_sort)
        QWidget.setTabOrder(self.message_sort, self.folder_list)
        QWidget.setTabOrder(self.folder_list, self.message_list)

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def set_messages(self, messages: list[dict[str, Any]]) -> None:
        self.set_mailbox({"folder": self.current_folder, "rows": messages, "total": len(messages), "offset": 0, "limit": self.page_limit})

    def set_folders(self, folders: list[dict[str, Any]]) -> None:
        selected = self.current_folder
        self.folder_list.blockSignals(True)
        self.folder_combo.blockSignals(True)
        self.folder_list.clear()
        self.folder_combo.clear()
        self.folders = {}
        visible_folders = [
            {"id": "__ALL__", "name": "Wszystkie wiadomości", "role": "all"},
            *[dict(folder) for folder in folders],
        ]
        for folder in visible_folders:
            identifier = str(folder.get("id") or "")
            if not identifier:
                continue
            self.folders[identifier] = dict(folder)
            item = QListWidgetItem(str(folder.get("name") or identifier))
            item.setData(Qt.ItemDataRole.UserRole, identifier)
            item.setToolTip(identifier)
            self.folder_list.addItem(item)
            self.folder_combo.addItem(str(folder.get("name") or identifier), identifier)
            if identifier == selected or (not selected and folder.get("role") == "inbox"):
                self.folder_list.setCurrentItem(item)
        if self.folder_list.currentRow() < 0 and self.folder_list.count():
            self.folder_list.setCurrentRow(0)
        current = self.folder_list.currentItem()
        if current is not None:
            self.current_folder = str(current.data(Qt.ItemDataRole.UserRole))
        self.folder_list.blockSignals(False)
        combo_index = self.folder_combo.findData(self.current_folder)
        if combo_index >= 0:
            self.folder_combo.setCurrentIndex(combo_index)
        self.folder_combo.blockSignals(False)

    def set_mailbox(self, page_value: object) -> None:
        if not isinstance(page_value, dict):
            return
        page = page_value
        folder = str(page.get("folder") or self.current_folder)
        self.view_folder = folder
        page_query = str(page.get("query") or "").strip()
        rows: list[dict[str, Any]] = []
        incoming: set[str] = set()
        for item in page.get("rows", []):
            if not isinstance(item, dict):
                continue
            value = dict(item)
            uid = str(value.get("uid") or "")
            if not uid or uid in incoming:
                continue
            incoming.add(uid)
            rows.append(value)
        offset = int(page.get("offset") or 0)
        # Wyszukiwanie „we wszystkich folderach” nie zmienia folderu,
        # z którego użytkownik je uruchomił. Ręczne wybranie pozycji
        # „Wszystkie wiadomości” ma pustą frazę i pozostaje aktywne.
        if folder != "__ALL__" or not page_query or self.current_folder == "__ALL__":
            self.current_folder = folder
        # Lista przedstawia jedną, jasno opisaną stronę. Odwiedzone wiadomości
        # pozostają w ``rows`` jako pamięć podręczna szczegółów.
        self.all_rows = rows
        self.offset = offset
        try:
            self.page_limit = max(1, min(100, int(page.get("limit") or self.page_limit)))
        except (TypeError, ValueError):
            pass
        self.total = int(page.get("total") or len(self.all_rows))
        self.rows.update({str(item.get("uid", "")): item for item in rows})
        self._page_action = ""
        self.newer_button.setText("← Nowsze")
        self.older_button.setText("Starsze →")
        self._apply_view()
        self._update_pagination()
        query = page_query
        if query and not self.all_rows:
            self.status.setText(f"Nie znaleziono wiadomości dla: „{query}”.")
        elif query:
            start, end = self._visible_range()
            self.status.setText(
                f"Znaleziono {self.total} wiadomości dla: „{query}”. Pokazano {start}–{end}."
            )
        else:
            start, end = self._visible_range()
            if self.total:
                self.status.setText(f"Wyświetlane wiadomości {start}–{end} z {self.total}.")
            else:
                self.status.setText("Ten folder nie zawiera wiadomości.")

    def set_refresh_result(self, page_value: object) -> int:
        if not isinstance(page_value, dict):
            return 0
        previous = {str(item.get("uid") or "") for item in self.all_rows}
        self.set_mailbox(page_value)
        current = {str(item.get("uid") or "") for item in self.all_rows}
        added = len({uid for uid in current if uid and uid not in previous})
        if added:
            self.status.setText(
                "Dodano 1 nową wiadomość."
                if added == 1
                else f"Dodano {added} nowe wiadomości."
                if 2 <= added <= 4
                else f"Dodano {added} nowych wiadomości."
            )
        else:
            self.status.setText("Brak nowych wiadomości.")
        return added

    def reset(self) -> None:
        self.rows.clear()
        self.current_uid = ""
        self.attachments_by_uid.clear()
        self.bodies_by_uid.clear()
        self.all_rows.clear()
        self.folders.clear()
        self.current_folder = "INBOX"
        self.view_folder = "INBOX"
        self.offset = 0
        self.total = 0
        self._page_action = ""
        self.folder_list.clear()
        self.folder_combo.clear()
        self.message_list.clear()
        self.search.clear()
        self.pagination_bar.hide()
        self.pagination_label.setText("Zakres wiadomości pojawi się po pobraniu poczty.")
        self.newer_button.setEnabled(False)
        self.older_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Odśwież pocztę")
        self.semantic_button.setEnabled(False)
        self.status.setText("Połącz konto w Ustawieniach, aby zobaczyć wiadomości.")

    def select_message(self, uid: str) -> bool:
        for index in range(self.message_list.count()):
            item = self.message_list.item(index)
            if item is not None and str(item.data(0, Qt.ItemDataRole.UserRole)) == str(uid):
                self.message_list.setCurrentItem(item)
                self._open_message_item(item)
                return True
        return False

    @Slot()
    @Slot(int)
    def _apply_view(self, _unused: int = 0) -> None:
        self.message_list.setUpdatesEnabled(False)
        self.message_list.clear()
        mode = str(self.message_filter.currentData() or "all")
        values = list(self.all_rows)
        if mode == "unread":
            values = [row for row in values if row.get("unread")]
        elif mode == "attachments":
            values = [row for row in values if row.get("has_attachments")]
        sort = str(self.message_sort.currentData() or "newest")
        if sort == "oldest":
            values.reverse()
        elif sort == "subject":
            values.sort(key=lambda row: str(row.get("subject") or "").casefold())
        for row in values:
            subject = str(row.get("subject", "(brak tematu)"))
            folder_info = self.folders.get(str(row.get("folder") or self.current_folder), {})
            sent = folder_info.get("role") == "sent"
            sender = str(row.get("recipient" if sent else "sender", "(brak adresu)"))
            date = str(row.get("date", "Data nieznana"))
            snippet = str(row.get("snippet") or "").strip()
            if len(snippet) > 220:
                snippet = snippet[:217].rsplit(" ", 1)[0] + "…"
            badges = []
            if row.get("unread"):
                badges.append("Nieprzeczytana")
            if row.get("has_attachments"):
                badges.append("Załącznik PDF")
            accessible_text = "\n".join(
                part for part in (" • ".join(badges), sender, subject, date, snippet) if part
            )
            # Jeden natywny wiersz QTreeWidget rysuje wszystkie kolumny. Nie
            # osadzamy osobnych QWidgetów, które podczas szybkiej wymiany stron
            # mogły na Windows na moment odłączyć się jako puste okna.
            status_parts = []
            if row.get("unread"):
                status_parts.append("Nowa")
            if row.get("has_attachments"):
                status_parts.append("Załącznik PDF")
            status_parts.append(date)
            item = QTreeWidgetItem([sender, subject, snippet, " · ".join(status_parts)])
            item.setData(0, Qt.ItemDataRole.UserRole, str(row.get("uid", "")))
            item.setData(0, Qt.ItemDataRole.AccessibleTextRole, accessible_text)
            item.setToolTip(1, subject)
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if row.get("unread"):
                bold = QFont(self.message_list.font())
                bold.setWeight(QFont.Weight.Bold)
                item.setFont(0, bold)
                item.setFont(1, bold)
            for column in range(4):
                item.setSizeHint(column, QSize(120, 52))
            self.message_list.addItem(item)
        self.message_list.setUpdatesEnabled(True)
        self.message_list.viewport().update()

    def _fit_message_rows(self) -> None:
        """Dopasowuje wysokość kart po faktycznej szerokości listy."""
        width = max(100, self.message_list.viewport().width() - 12)
        narrow = width < 690
        meta_width = min(330, max(180, int(width * 0.28)))
        content_width = max(180, width - meta_width)
        sender_width = max(100, int(content_width * (0.38 if narrow else 0.25)))
        subject_width = max(130, int(content_width * (0.62 if narrow else 0.31)))
        snippet_width = max(0, content_width - sender_width - subject_width)
        self.message_list.setColumnHidden(2, narrow)
        self.message_list.setColumnWidth(0, sender_width)
        self.message_list.setColumnWidth(1, subject_width)
        self.message_list.setColumnWidth(2, snippet_width)
        self.message_list.setColumnWidth(3, meta_width)
    def set_details(self, uid: str, body: str, attachments: list[str]) -> None:
        self.bodies_by_uid[uid] = body
        self.attachments_by_uid[uid] = list(attachments)

    def set_busy(self, busy: bool, text: str = "") -> None:
        if text:
            self.status.setText(text)
        self.message_list.setEnabled(not busy)
        searching = "wyszukiwanie" in text.lower()
        self.search_button.setEnabled(not busy)
        self.clear_search_button.setEnabled(not busy)
        self.search_button.setText("Wyszukiwanie…" if busy and searching else "Szukaj")
        refreshing = busy and "sprawdzanie nowych wiadomości" in text.lower()
        self.refresh_button.setEnabled(not busy)
        self.refresh_button.setText("Sprawdzanie nowych wiadomości…" if refreshing else "Odśwież pocztę")
        self.page_size.setEnabled(not busy)
        if busy:
            self.newer_button.setEnabled(False)
            self.older_button.setEnabled(False)
        else:
            self._page_action = ""
            self.newer_button.setText("← Nowsze")
            self.older_button.setText("Starsze →")
            self._update_pagination()
        if busy and self._page_action == "newer":
            self.newer_button.setText("Pobieranie nowszych…")
        elif busy and self._page_action == "older":
            self.older_button.setText("Pobieranie starszych…")

    def resizeEvent(self, event: QResizeEvent) -> None:
        compact = event.size().width() < 900
        self._compact = compact
        self.filter_toggle.setVisible(True)
        self.filter_panel.setVisible(self._filters_expanded)
        self.help_button.setVisible(not compact)
        self.semantic_button.setVisible(not compact)
        self.compact_folder_row.setVisible(compact)
        self.folder_card.setVisible(not compact)
        if self.splitter.orientation() != Qt.Orientation.Horizontal:
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
        self.folder_card.setMaximumHeight(16777215)
        self.mail_list_card.setMaximumHeight(16777215)
        self._fit_message_rows()
        super().resizeEvent(event)

    @Slot()
    def _toggle_filters(self) -> None:
        self._filters_expanded = not self._filters_expanded
        self.filter_panel.setVisible(self._filters_expanded)
        self.filter_toggle.setText("Ukryj filtry i sortowanie" if self._filters_expanded else "Filtry i sortowanie")

    @Slot(object)
    def _open_message_item(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        self.current_uid = str(item.data(0, Qt.ItemDataRole.UserRole))
        self.message_selected.emit(self.current_uid)

    @Slot(QListWidgetItem, QListWidgetItem)
    def _folder_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        self.current_folder = str(current.data(Qt.ItemDataRole.UserRole))
        self.view_folder = self.current_folder
        self.folder_combo.blockSignals(True)
        combo_index = self.folder_combo.findData(self.current_folder)
        if combo_index >= 0:
            self.folder_combo.setCurrentIndex(combo_index)
        self.folder_combo.blockSignals(False)
        self.offset = 0
        self.mailbox_requested.emit(self.current_folder, 0, self.page_limit, self.search.text().strip())

    @Slot(int)
    def _folder_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        folder = str(self.folder_combo.itemData(index) or "")
        if not folder or folder == self.current_folder:
            return
        self.current_folder = folder
        self.view_folder = folder
        self.folder_list.blockSignals(True)
        for row in range(self.folder_list.count()):
            item = self.folder_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) == folder:
                self.folder_list.setCurrentItem(item)
                break
        self.folder_list.blockSignals(False)
        self.offset = 0
        self.mailbox_requested.emit(folder, 0, self.page_limit, self.search.text().strip())

    @Slot()
    def _search_messages(self) -> None:
        query = self.search.text().strip()
        folder = "__ALL__" if query and self.search_scope.currentData() == "all" else self.current_folder
        self.view_folder = folder
        self._page_action = "search"
        self.set_busy(True, "Wyszukiwanie w całej skrzynce…" if folder == "__ALL__" else "Wyszukiwanie wiadomości…")
        self.mailbox_requested.emit(folder, 0, self.page_limit, query)

    @Slot()
    def _clear_search(self) -> None:
        self.search.clear()
        self.view_folder = self.current_folder
        self._page_action = "search"
        self.set_busy(True, "Pobieranie wiadomości…")
        self.mailbox_requested.emit(self.current_folder, 0, self.page_limit, "")

    @Slot()
    def _load_more(self) -> None:
        self._older_page()

    @Slot()
    def _older_page(self) -> None:
        next_offset = self.offset + self.page_limit
        if next_offset >= self.total:
            return
        self._page_action = "older"
        self.set_busy(True, "Pobieranie starszych wiadomości…")
        self.mailbox_requested.emit(
            self.view_folder, next_offset, self.page_limit, self.search.text().strip()
        )

    @Slot()
    def _newer_page(self) -> None:
        if self.offset <= 0:
            return
        previous_offset = max(0, self.offset - self.page_limit)
        self._page_action = "newer"
        self.set_busy(True, "Pobieranie nowszych wiadomości…")
        self.mailbox_requested.emit(
            self.view_folder, previous_offset, self.page_limit, self.search.text().strip()
        )

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
        self._page_action = "search"
        self.set_busy(True, "Zmienianie liczby wiadomości na stronie…")
        self.mailbox_requested.emit(
            self.view_folder, 0, self.page_limit, self.search.text().strip()
        )

    def _visible_range(self) -> tuple[int, int]:
        if not self.all_rows or self.total <= 0:
            return 0, 0
        return self.offset + 1, min(self.total, self.offset + len(self.all_rows))

    def _update_pagination(self) -> None:
        start, end = self._visible_range()
        pages = max(1, (self.total + self.page_limit - 1) // self.page_limit)
        current = min(pages, self.offset // self.page_limit + 1)
        self.pagination_label.setText(
            f"Wiadomości {start}–{end} z {self.total}  •  strona {current} z {pages}"
            if self.total else "Brak wiadomości na tej stronie."
        )
        self.pagination_bar.setVisible(True)
        self.newer_button.setEnabled(self.offset > 0)
        self.older_button.setEnabled(self.offset + len(self.all_rows) < self.total)
        combo_index = self.page_size.findData(self.page_limit)
        if combo_index >= 0 and combo_index != self.page_size.currentIndex():
            self.page_size.blockSignals(True)
            self.page_size.setCurrentIndex(combo_index)
            self.page_size.blockSignals(False)

    @Slot()
    def _refresh_messages(self) -> None:
        query = self.search.text().strip()
        folder = "__ALL__" if query and self.search_scope.currentData() == "all" else self.current_folder
        limit = max(self.page_limit, min(100, len(self.all_rows) or self.page_limit))
        self.refresh_requested.emit(folder, limit, query)

class MessagePage(QWidget):
    back_requested = Signal()
    analyze_requested = Signal(str, str, str)
    save_attachment_requested = Signal(str, int)
    reply_requested = Signal(str)
    help_requested = Signal()
    tasks_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.current_uid = ""
        self.current_body = ""
        self.current_row: dict[str, Any] = {}
        self.attachment_names: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(26, 20, 26, 12)
        self.back_button = QPushButton("← Wróć do poczty")
        self.back_button.setObjectName("SecondaryButton")
        self.back_button.clicked.connect(self.back_requested)
        header.addWidget(self.back_button)
        header.addStretch(1)
        help_button = QPushButton("Potrzebuję pomocy")
        help_button.setObjectName("QuietButton")
        help_button.clicked.connect(self.help_requested)
        header.addWidget(help_button)
        outer.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page_content = QWidget()
        root = QVBoxLayout(page_content)
        root.setContentsMargins(26, 8, 26, 28)
        root.setSpacing(14)

        title = QLabel("Wiadomość")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        self.status = QLabel("Wybierz wiadomość w zakładce Poczta.")
        self.status.setObjectName("StatusInfo")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        message_card, content = card()
        self.message_card = message_card
        message_card.setMinimumWidth(0)
        message_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.subject_label = QLabel("Wybierz wiadomość")
        self.subject_label.setObjectName("SectionTitle")
        self.subject_label.setWordWrap(True)
        content.addWidget(self.subject_label)
        self.sender_label = QLabel("")
        self.sender_label.setObjectName("Muted")
        self.sender_label.setWordWrap(True)
        content.addWidget(self.sender_label)
        self.recipient_label = QLabel("")
        self.recipient_label.setObjectName("Muted")
        self.recipient_label.setWordWrap(True)
        content.addWidget(self.recipient_label)
        self.preview = QTextBrowser()
        self.preview.setObjectName("MailPreview")
        self.preview.setPlaceholderText("Treść wiadomości pojawi się tutaj.")
        self.preview.setMinimumHeight(280)
        content.addWidget(self.preview)
        root.addWidget(message_card)

        attachment_card, attachment_content = card()
        self.attachment_card = attachment_card
        attachment_card.setMinimumWidth(0)
        attachment_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.attachment_title = QLabel("Załączniki PDF")
        self.attachment_title.setObjectName("SectionTitle")
        self.attachment_title.setContentsMargins(0, 4, 0, 6)
        attachment_content.addWidget(self.attachment_title)
        self.attachments = QListWidget()
        self.attachments.setMinimumHeight(96)
        self.attachments.setMaximumHeight(150)
        attachment_content.addWidget(self.attachments)

        actions = QVBoxLayout()
        self.save_button = QPushButton("Zapisz załącznik PDF")
        self.save_button.setObjectName("QuietButton")
        self.save_button.clicked.connect(self._save_attachment)
        self.reply_button = QPushButton("Odpowiedz")
        self.reply_button.setObjectName("SecondaryButton")
        self.reply_button.clicked.connect(self._reply)
        self.analyze_button = QPushButton("Wyjaśnij wiadomość")
        self.analyze_button.clicked.connect(self._analyze)
        self.tasks_button = QPushButton("Zobacz zadania")
        self.tasks_button.setObjectName("SecondaryButton")
        self.tasks_button.clicked.connect(self.tasks_requested)
        self.tasks_button.hide()
        actions.addWidget(self.save_button)
        actions.addWidget(self.reply_button)
        actions.addWidget(self.analyze_button)
        actions.addWidget(self.tasks_button)
        attachment_content.addLayout(actions)
        root.addWidget(attachment_card)
        root.addStretch(1)
        self.scroll.setWidget(page_content)
        outer.addWidget(self.scroll, 1)
        self.set_actions_enabled(False)

    def show_message(
        self, uid: str, row: dict[str, Any], body: str = "", attachments: list[str] | None = None,
    ) -> None:
        self.current_uid = uid
        self.current_row = dict(row)
        self.subject_label.setText(str(row.get("subject") or "(brak tematu)"))
        self.sender_label.setText(
            f"Od: {row.get('sender') or '(brak nadawcy)'}   •   {row.get('date') or 'Data nieznana'}"
        )
        self.recipient_label.setText(f"Do: {row.get('recipient') or '(brak odbiorcy)'}")
        self.current_body = body
        self.preview.setPlainText(body)
        self.preview.setPlaceholderText("Pobieranie pełnej treści wiadomości…" if not body else "")
        self.attachments.clear()
        self.attachment_names = list(attachments or [])
        self.attachments.addItems(self.attachment_names)
        if self.attachment_names:
            self.attachments.setCurrentRow(0)
        self.status.setText("Wiadomość jest gotowa." if body else "Pobieranie wiadomości…")
        self.status.setObjectName("StatusSuccess" if body else "StatusInfo")
        self.style().unpolish(self.status)
        self.style().polish(self.status)
        self.set_actions_enabled(bool(body))

    def set_details(self, uid: str, body: str, attachments: list[str]) -> None:
        if uid != self.current_uid:
            return
        self.current_body = body
        self.preview.setPlainText(body)
        self.attachments.clear()
        self.attachment_names = list(attachments)
        self.attachments.addItems(self.attachment_names)
        if self.attachment_names:
            self.attachments.setCurrentRow(0)
        self.status.setText("Wiadomość jest gotowa.")
        self.status.setObjectName("StatusSuccess")
        self.style().unpolish(self.status)
        self.style().polish(self.status)
        self.set_actions_enabled(True)

    def reset(self) -> None:
        self.current_uid = ""
        self.current_body = ""
        self.current_row = {}
        self.attachment_names = []
        self.subject_label.setText("Wybierz wiadomość")
        self.sender_label.clear()
        self.recipient_label.clear()
        self.preview.clear()
        self.preview.setPlaceholderText("Treść wiadomości pojawi się tutaj.")
        self.attachments.clear()
        self.status.setText("Zaloguj się, aby zobaczyć wiadomości.")
        self.status.setObjectName("StatusInfo")
        self.tasks_button.hide()
        self.set_actions_enabled(False)

    def set_actions_enabled(self, enabled: bool) -> None:
        ready = enabled and bool(self.current_body)
        self.analyze_button.setEnabled(ready)
        self.reply_button.setEnabled(ready)
        self.save_button.setEnabled(enabled and bool(self.attachment_names))

    def set_has_tasks(self, available: bool) -> None:
        self.tasks_button.setVisible(available)
        self.tasks_button.setEnabled(available)

    def set_busy(self, busy: bool, text: str = "") -> None:
        if text:
            self.status.setText(text)
        self.set_actions_enabled(not busy)
        lowered = text.lower()
        self.save_button.setText("Zapisywanie PDF…" if busy and "zapisywanie załącznika" in lowered else "Zapisz załącznik PDF")
        self.reply_button.setText("Przygotowywanie odpowiedzi…" if busy and "przygotowywanie odpowiedzi" in lowered else "Odpowiedz")

    def set_analysis_busy(self, busy: bool) -> None:
        self.analyze_button.setEnabled(not busy and bool(self.current_body))
        self.analyze_button.setText("Wyjaśniam wiadomość…" if busy else "Wyjaśnij wiadomość")

    @Slot()
    def _analyze(self) -> None:
        if self.current_uid and self.current_body:
            self.analyze_requested.emit(
                self.current_uid,
                str(self.current_row.get("subject") or "(brak tematu)"),
                self.current_body,
            )

    @Slot()
    def _save_attachment(self) -> None:
        index = self.attachments.currentRow()
        if self.current_uid and index >= 0:
            self.save_attachment_requested.emit(self.current_uid, index)

    @Slot()
    def _reply(self) -> None:
        if self.current_uid and self.current_body:
            self.reply_requested.emit(self.current_uid)


class TasksPage(QWidget):
    progress_changed = Signal(str, list)
    device_action_requested = Signal(str, object)
    reply_action_requested = Signal(object)
    source_requested = Signal(str)

    CONFIDENCE = {"high": "wysoka", "medium": "średnia", "low": "niska"}

    def __init__(self) -> None:
        super().__init__()
        self.uid = ""
        self.analysis: dict[str, Any] = {}
        self.checkboxes: dict[str, QCheckBox] = {}
        self.task_order: list[str] = []
        self.task_names: dict[str, str] = {}
        self.step_index = 0
        self.attachment_names: list[str] = []
        self.mail_uid = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.root = QVBoxLayout(content)
        self.root.setContentsMargins(26, 24, 26, 30)
        self.root.setSpacing(16)
        heading = QHBoxLayout()
        title = QLabel("Moje zadania")
        title.setObjectName("PageTitle")
        heading.addWidget(title, 1)
        self.source_button = QPushButton("Otwórz wiadomość źródłową")
        self.source_button.setObjectName("QuietButton")
        self.source_button.clicked.connect(lambda: self.source_requested.emit(self.mail_uid))
        self.source_button.hide()
        heading.addWidget(self.source_button)
        self.root.addLayout(heading)

        intro, intro_layout = card()
        intro_layout.addWidget(self._section("Podsumowanie wiadomości"))
        self.source_message = QLabel("")
        self.source_message.setObjectName("Muted")
        self.source_message.setWordWrap(True)
        self.source_message.hide()
        intro_layout.addWidget(self.source_message)
        self.summary = QLabel("Po analizie wiadomości pojawi się tutaj prosty plan działania.")
        self.summary.setWordWrap(True)
        intro_layout.addWidget(self.summary)
        self.root.addWidget(intro)

        events, events_layout = card()
        events_layout.addWidget(self._section("Wydarzenia"))
        self.events_label = QLabel("Nie przeanalizowano jeszcze wiadomości.")
        self.events_label.setWordWrap(True)
        events_layout.addWidget(self.events_label)
        self.root.addWidget(events)

        tasks, tasks_outer = card()
        tasks_outer.addWidget(self._section("Plan działania"))
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("0 z 0 wykonanych")
        tasks_outer.addWidget(self.progress)
        self.task_holder = QWidget()
        self.task_layout = QVBoxLayout(self.task_holder)
        self.task_layout.setContentsMargins(0, 4, 0, 0)
        self.task_layout.setSpacing(10)
        tasks_outer.addWidget(self.task_holder)
        self.root.addWidget(tasks)

        steps, step_layout = card()
        step_layout.addWidget(self._section("Prowadzenie krok po kroku"))
        self.step_counter = QLabel("Brak kroków")
        self.step_counter.setObjectName("Muted")
        step_layout.addWidget(self.step_counter)
        self.step_text = QLabel("Najpierw przeanalizuj wybraną wiadomość.")
        self.step_text.setWordWrap(True)
        self.step_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        step_layout.addWidget(self.step_text)
        nav = QHBoxLayout()
        self.previous_button = QPushButton("Wstecz")
        self.previous_button.setObjectName("SecondaryButton")
        self.previous_button.clicked.connect(self.previous_step)
        self.help_button = QPushButton("Potrzebuję pomocy")
        self.help_button.setObjectName("SecondaryButton")
        self.help_button.clicked.connect(self.show_help)
        self.next_button = QPushButton("Dalej")
        self.next_button.clicked.connect(self.next_step)
        nav.addWidget(self.previous_button)
        nav.addWidget(self.help_button)
        nav.addStretch(1)
        nav.addWidget(self.next_button)
        step_layout.addLayout(nav)
        self.step_device_button = QPushButton("")
        self.step_device_button.clicked.connect(self._current_device_action)
        self.step_device_button.hide()
        step_layout.addWidget(self.step_device_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.root.addWidget(steps)

        notes, notes_layout = card()
        notes_layout.addWidget(self._section("Ważne informacje"))
        self.notes_label = QLabel("Ostrzeżenia i informacje o niepewności pojawią się tutaj.")
        self.notes_label.setWordWrap(True)
        notes_layout.addWidget(self.notes_label)
        self.root.addWidget(notes)
        self.root.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._update_step()

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def set_analysis(
        self,
        message_identity: str,
        data: dict[str, Any],
        completed: set[str] | None = None,
        source_message: str = "",
        attachment_names: list[str] | None = None,
        mail_uid: str = "",
    ) -> None:
        self.uid = message_identity
        self.analysis = data or {}
        self.attachment_names = list(attachment_names or [])
        self.mail_uid = mail_uid
        self.source_button.setVisible(bool(mail_uid))
        self.source_message.setText(f"Wiadomość: {source_message}" if source_message else "")
        self.source_message.setVisible(bool(source_message))
        self.summary.setText(str(self.analysis.get("summary") or "Brak podsumowania."))
        events = self.analysis.get("events") if isinstance(self.analysis.get("events"), list) else []
        if events:
            lines = []
            for event in events:
                title = str(event.get("title") or "Wydarzenie")
                start = format_polish_datetime(event.get("start"))
                location = str(event.get("location") or "").strip()
                line = f"• {title} — {start}"
                if location:
                    line += f"\n  Miejsce: {location}"
                lines.append(line)
            self.events_label.setText("\n\n".join(lines))
        else:
            self.events_label.setText("W tej wiadomości nie znaleziono wydarzenia z konkretną datą.")

        clear_layout(self.task_layout)
        self.checkboxes.clear()
        tasks = self.analysis.get("tasks") if isinstance(self.analysis.get("tasks"), list) else []
        self.task_order = [stable_task_key(task, index + 1) for index, task in enumerate(tasks)]
        self.task_names = {
            str(task.get("id") or f"krok-{index + 1}"): str(task.get("action") or f"Krok {index + 1}")
            for index, task in enumerate(tasks)
        }
        completed = completed or set()
        for index, task in enumerate(tasks):
            task_id = self.task_order[index]
            frame, layout = card()
            frame.setProperty("taskCard", True)
            action = str(task.get("action") or f"Krok {index + 1}")
            check = QCheckBox(f"{index + 1}. {action}")
            check.setChecked(task_id in completed)
            check.setFont(QFont("Segoe UI", -1, QFont.Weight.DemiBold))
            check.toggled.connect(partial(self._task_toggled, task_id))
            layout.addWidget(check)
            details = self._task_details(task, include_source=False)
            if details:
                label = QLabel(details)
                label.setWordWrap(True)
                label.setObjectName("Muted")
                layout.addWidget(label)
            action_type = task_device_action(task)
            if action_type != "none":
                text = "Wydrukuj potrzebny PDF" if action_type == "print" else "Zeskanuj dokument"
                action_button = QPushButton(text)
                action_button.clicked.connect(
                    lambda _checked=False, kind=action_type, item=task: self.device_action_requested.emit(kind, item)
                )
                layout.addWidget(action_button, 0, Qt.AlignmentFlag.AlignLeft)
            action_key = action.lower()
            if any(word in action_key for word in ("odesłać", "wysłać", "odpisać", "potwierdzić", "odpowiedzi")):
                reply_button = QPushButton("Przygotuj odpowiedź")
                reply_button.clicked.connect(
                    lambda _checked=False, item=task: self.reply_action_requested.emit(item)
                )
                layout.addWidget(reply_button, 0, Qt.AlignmentFlag.AlignLeft)
            self.task_layout.addWidget(frame)
            self.checkboxes[task_id] = check
        if not tasks:
            label = QLabel("Nie znaleziono czynności do wykonania.")
            label.setWordWrap(True)
            self.task_layout.addWidget(label)

        completeness = self.analysis.get("completeness") if isinstance(self.analysis.get("completeness"), dict) else {}
        warnings = [str(item) for item in self.analysis.get("warnings", []) if str(item).strip()]
        missing = [str(item) for item in completeness.get("missing", []) if str(item).strip()]
        conflicts = [str(item) for item in completeness.get("conflicts", []) if str(item).strip()]
        completeness_status = str(completeness.get("status") or "uncertain")
        important = [str(item) for item in self.analysis.get("important_notes", []) if str(item).strip()]
        notes: list[str] = []
        if important:
            notes.append("Ważne wskazówki z wiadomości:\n" + "\n".join(f"• {item}" for item in important))
        if warnings:
            notes.append("Do sprawdzenia:\n" + "\n".join(f"• {item}" for item in warnings))
        if missing:
            notes.append("Brakujące informacje:\n" + "\n".join(f"• {item}" for item in missing))
        if conflicts:
            notes.append("Sprzeczne informacje:\n" + "\n".join(f"• {item}" for item in conflicts))
        if completeness_status == "uncertain" and not warnings and not missing and not conflicts:
            notes.append(
                "Analiza znalazła potrzebne informacje, ale część odpowiedzi modelu została automatycznie poprawiona. "
                "Przed wykonaniem kroków porównaj je z treścią wiadomości."
            )
        elif completeness_status == "incomplete" and not missing:
            notes.append("Wiadomość może nie zawierać wszystkich informacji potrzebnych do wykonania planu.")
        elif not notes:
            notes.append("Plan jest kompletny. Mimo to przed wykonaniem zadania porównaj go z treścią wiadomości.")
        notes.append("Aplikacja niczego nie wysyła automatycznie.")
        self.notes_label.setText("\n\n".join(notes))
        self.step_index = 0
        self._update_progress()
        self._update_step()

    def reset(self) -> None:
        self.uid = ""
        self.analysis = {}
        self.task_order = []
        self.task_names = {}
        self.step_index = 0
        self.attachment_names = []
        self.mail_uid = ""
        self.source_button.hide()
        self.source_message.clear()
        self.source_message.hide()
        self.summary.setText("Po zalogowaniu przeanalizuj wiadomość, aby zobaczyć prosty plan działania.")
        self.events_label.setText("Nie przeanalizowano jeszcze wiadomości.")
        clear_layout(self.task_layout)
        self.checkboxes.clear()
        placeholder = QLabel("Zadania pojawią się po przeanalizowaniu wiadomości.")
        placeholder.setWordWrap(True)
        self.task_layout.addWidget(placeholder)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("0 z 0 wykonanych")
        self.notes_label.setText("Ostrzeżenia i informacje o niepewności pojawią się tutaj.")
        self._update_step()

    def _task_details(self, task: dict[str, Any], include_source: bool) -> str:
        lines: list[str] = []
        deadline = str(task.get("deadline") or "")
        if deadline:
            lines.append(f"Termin: {format_polish_datetime(deadline)}")
        else:
            lines.append("Termin: nie podano")
        quantity = task.get("quantity")
        if isinstance(quantity, int) and quantity > 0:
            lines.append(f"Liczba egzemplarzy: {quantity}")
        condition = str(task.get("condition") or "").strip()
        if condition:
            lines.append(f"Warunek: {condition}")
        instruction = str(task.get("instruction") or "").strip()
        if instruction:
            lines.append(f"Wskazówka: {instruction}")
        dependencies = [str(item) for item in task.get("depends_on", []) if str(item)]
        dependency_names = [self.task_names[item] for item in dependencies if item in self.task_names]
        if dependency_names:
            lines.append("Najpierw wykonaj: " + ", ".join(dependency_names))
        confidence = self.CONFIDENCE.get(str(task.get("confidence", "")), "nieokreślona")
        lines.append(f"Pewność analizy: {confidence}")
        if include_source:
            source = str(task.get("source_quote") or "").strip()
            if source:
                lines.append(f"Fragment wiadomości: „{source}”")
        return "\n".join(lines)

    @Slot(bool)
    def _task_toggled(self, _task_id: str, _checked: bool) -> None:
        self._update_progress()
        completed = [task_id for task_id, box in self.checkboxes.items() if box.isChecked()]
        self.progress_changed.emit(self.uid, completed)

    def _update_progress(self) -> None:
        total = len(self.checkboxes)
        done = sum(1 for box in self.checkboxes.values() if box.isChecked())
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress.setFormat(f"{done} z {total} wykonanych")

    @Slot()
    def previous_step(self) -> None:
        if self.task_order:
            self.step_index = max(0, self.step_index - 1)
            self._update_step()

    @Slot()
    def next_step(self) -> None:
        if self.task_order:
            self.step_index = min(len(self.task_order) - 1, self.step_index + 1)
            self._update_step()

    def _current_task(self) -> dict[str, Any] | None:
        tasks = self.analysis.get("tasks") if isinstance(self.analysis.get("tasks"), list) else []
        return tasks[self.step_index] if tasks and 0 <= self.step_index < len(tasks) else None

    def _update_step(self) -> None:
        task = self._current_task()
        if task is None:
            self.step_counter.setText("Brak kroków")
            self.step_text.setText("Najpierw przeanalizuj wybraną wiadomość.")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.help_button.setEnabled(False)
            self.step_device_button.hide()
            return
        total = len(self.task_order)
        title = str(task.get("action") or f"Krok {self.step_index + 1}")
        self.step_counter.setText(f"Krok {self.step_index + 1} z {total}")
        sections = [f"Co zrobić:\n{title}", self._task_details(task, include_source=False)]
        action_key = (title + " " + str(task.get("instruction") or "")).lower()
        if self.attachment_names and any(word in action_key for word in ("dokument", "formularz", "pdf", "wydruk", "skan")):
            sections.append("Potrzebny dokument: " + ", ".join(self.attachment_names))
        source = str(task.get("source_quote") or "").strip()
        if source:
            sections.append(f"Dlaczego ten krok jest potrzebny:\n„{source}”")
        self.step_text.setText("\n\n".join(value for value in sections if value))
        self.previous_button.setEnabled(self.step_index > 0)
        self.next_button.setEnabled(self.step_index < total - 1)
        self.help_button.setEnabled(True)
        device_action = task_device_action(task)
        self.step_device_button.setVisible(device_action != "none")
        if device_action != "none":
            self.step_device_button.setText("Wydrukuj potrzebny PDF" if device_action == "print" else "Zeskanuj dokument")
            self.step_device_button.setObjectName("SeniorContinue")

    @Slot()
    def _current_device_action(self) -> None:
        task = self._current_task()
        if task is not None:
            action = task_device_action(task)
            if action != "none":
                self.device_action_requested.emit(action, task)

    @Slot()
    def show_help(self) -> None:
        task = self._current_task()
        if task is None:
            return
        action = str(task.get("action") or "Ten krok")
        source = str(task.get("source_quote") or "Brak fragmentu źródłowego.")
        QMessageBox.information(
            self,
            "Pomoc do tego kroku",
            f"Co zrobić:\n{action}\n\n{self._task_details(task, include_source=False)}"
            f"\n\nSkąd to wynika:\n„{source}”\n\nAplikacja niczego nie wysyła automatycznie.",
        )


class LoginRequiredPage(QWidget):
    login_requested = Signal()

    PAGE_NAMES = {
        0: "wiadomości",
        1: "zadania",
        3: "odpowiedzi",
        4: "dokumenty",
        5: "wiadomość",
        6: "podgląd dokumentu",
    }

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.addStretch(1)
        panel, content = card()
        panel.setMaximumWidth(680)
        title = QLabel("Najpierw zaloguj się do poczty")
        title.setObjectName("PageTitle")
        title.setWordWrap(True)
        content.addWidget(title)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        content.addWidget(self.message)
        note = QLabel("Adres e-mail może być zapamiętany, ale wiadomości i dokumenty są pokazywane dopiero po udanym połączeniu z WP Pocztą.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        content.addWidget(note)
        button = QPushButton("Przejdź do logowania")
        button.clicked.connect(self.login_requested)
        content.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(panel)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(2)
        self.set_target(0)

    def set_target(self, index: int) -> None:
        name = self.PAGE_NAMES.get(index, "dane konta")
        self.message.setText(f"Połącz konto w Ustawieniach, aby zobaczyć {name}.")


class SettingsPage(QWidget):
    login_requested = Signal(str, str)
    ollama_check_requested = Signal()
    appearance_changed = Signal(int, bool)
    backup_requested = Signal()
    restore_requested = Signal()
    backup_configuration_changed = Signal(str, bool, int)
    mode_changed = Signal(bool)
    wizard_requested = Signal()

    FONT_VALUES = {
        "Mniejszy": 90,
        "Standardowy": 100,
        "Duży": 120,
        "Bardzo duży": 140,
    }

    def __init__(self, state: AppSettings) -> None:
        super().__init__()
        self.state = state
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(16)
        title = QLabel("Ustawienia")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        account, account_layout = card()
        account_layout.addWidget(self._section("Konto WP Poczta"))
        note = QLabel("Hasło jest używane tylko podczas tej sesji i nie zostanie zapisane.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        account_layout.addWidget(note)
        form = QFormLayout()
        form.setSpacing(12)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.address = QLineEdit(state.address)
        self.address.setAccessibleName("Adres e-mail konta WP")
        self.address.setPlaceholderText("np. imie.nazwisko@wp.pl")
        self.password = QLineEdit()
        self.password.setAccessibleName("Hasło do poczty")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Hasło do poczty")
        form.addRow("Adres e-mail:", self.address)
        form.addRow("Hasło:", self.password)
        account_layout.addLayout(form)
        self.login_button = QPushButton("Połącz i pobierz wiadomości")
        self.login_button.clicked.connect(self._login)
        account_layout.addWidget(self.login_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.account_status = QLabel("Konto nie jest połączone.")
        self.account_status.setObjectName("Muted")
        account_layout.addWidget(self.account_status)
        root.addWidget(account)

        appearance, appearance_layout = card()
        appearance_layout.addWidget(self._section("Czytelność"))
        appearance_form = QFormLayout()
        appearance_form.setSpacing(12)
        appearance_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        appearance_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        appearance_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.font_size = QComboBox()
        self.font_size.addItems(list(self.FONT_VALUES))
        current_label = min(self.FONT_VALUES, key=lambda key: abs(self.FONT_VALUES[key] - state.font_scale))
        self.font_size.setCurrentText(current_label)
        self.contrast = QCheckBox("Zwiększony kontrast")
        self.contrast.setChecked(state.high_contrast)
        appearance_form.addRow("Rozmiar tekstu:", self.font_size)
        appearance_form.addRow("Wygląd:", self.contrast)
        appearance_layout.addLayout(appearance_form)
        self.font_size.currentTextChanged.connect(self._appearance)
        self.contrast.toggled.connect(self._appearance)
        root.addWidget(appearance)

        mode, mode_layout = card()
        mode_layout.addWidget(self._section("Sposób wyświetlania aplikacji"))
        mode_note = QLabel(
            "Tryb prosty pokazuje tylko najważniejsze czynności. Tryb zaawansowany udostępnia wszystkie ustawienia."
        )
        mode_note.setObjectName("Muted")
        mode_note.setWordWrap(True)
        mode_layout.addWidget(mode_note)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Tryb prosty", True)
        self.mode_combo.addItem("Tryb zaawansowany", False)
        self.mode_combo.setCurrentIndex(0 if state.simple_mode else 1)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        mode_layout.addWidget(self.mode_combo)
        self.wizard_button = QPushButton("Uruchom ponownie kreator konfiguracji")
        self.wizard_button.setObjectName("SecondaryButton")
        self.wizard_button.clicked.connect(self.wizard_requested)
        mode_layout.addWidget(self.wizard_button, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(mode)

        ollama, ollama_layout = card()
        self.ollama_card = ollama
        ollama_layout.addWidget(self._section("Lokalna analiza Ollama"))
        self.ollama_status = QLabel(f"Model: {MODEL_NAME}. Treść wiadomości pozostaje na tym komputerze.")
        self.ollama_status.setWordWrap(True)
        ollama_layout.addWidget(self.ollama_status)
        self.ollama_button = QPushButton("Sprawdź Ollamę")
        self.ollama_button.setObjectName("SecondaryButton")
        self.ollama_button.clicked.connect(self.ollama_check_requested)
        ollama_layout.addWidget(self.ollama_button, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(ollama)

        self.devices = DevicesPanel(state)
        root.addWidget(self.devices)

        backup, backup_layout = card()
        self.backup_card = backup
        backup_layout.addWidget(self._section("Kopie zapasowe"))
        backup_note = QLabel(
            "Kopia obejmuje postęp, ustawienia, bibliotekę, szkice i lokalne dokumenty. "
            "Jest szyfrowana przez Windows i może ją odczytać tylko to samo konto użytkownika."
        )
        backup_note.setObjectName("Muted")
        backup_note.setWordWrap(True)
        backup_layout.addWidget(backup_note)
        directory_row = QHBoxLayout()
        self.backup_directory = QLineEdit(state.backup_directory)
        self.backup_directory.setReadOnly(True)
        choose_directory = QPushButton("Wybierz katalog")
        choose_directory.setObjectName("SecondaryButton")
        choose_directory.clicked.connect(self._choose_backup_directory)
        directory_row.addWidget(self.backup_directory, 1)
        directory_row.addWidget(choose_directory)
        backup_layout.addLayout(directory_row)
        options = QFormLayout()
        options.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.automatic_backup = QCheckBox("Twórz automatycznie raz dziennie")
        self.automatic_backup.setChecked(state.automatic_backup)
        self.backup_keep = QSpinBox()
        self.backup_keep.setRange(1, 30)
        self.backup_keep.setValue(state.backup_keep)
        self.backup_keep.setSuffix(" kopii")
        options.addRow("Automatyczne kopie:", self.automatic_backup)
        options.addRow("Przechowuj:", self.backup_keep)
        backup_layout.addLayout(options)
        self.automatic_backup.toggled.connect(self._backup_configuration)
        self.backup_keep.valueChanged.connect(self._backup_configuration)
        backup_buttons = QHBoxLayout()
        self.backup_button = QPushButton("Utwórz kopię zapasową")
        self.backup_button.clicked.connect(self.backup_requested)
        self.restore_button = QPushButton("Przywróć dane z kopii")
        self.restore_button.setObjectName("SecondaryButton")
        self.restore_button.clicked.connect(self.restore_requested)
        backup_buttons.addWidget(self.backup_button)
        backup_buttons.addWidget(self.restore_button)
        backup_layout.addLayout(backup_buttons)
        self.backup_status = QLabel("Ostatnia udana kopia: " + (state.last_backup_date or "jeszcze nie utworzono"))
        self.backup_status.setObjectName("Muted")
        self.backup_status.setWordWrap(True)
        backup_layout.addWidget(self.backup_status)
        root.addWidget(backup)

        diagnostic, diagnostic_layout = card()
        self.diagnostic_card = diagnostic
        self.diagnostic_toggle = QToolButton()
        self.diagnostic_toggle.setText("Pokaż informacje techniczne")
        self.diagnostic_toggle.setCheckable(True)
        self.diagnostic_toggle.toggled.connect(self._toggle_diagnostics)
        diagnostic_layout.addWidget(self.diagnostic_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(150)
        self.diagnostics.setPlainText(f"Model: {MODEL_NAME}\nOllama: {OLLAMA_URL}\nInterfejs: PySide6")
        self.diagnostics.hide()
        diagnostic_layout.addWidget(self.diagnostics)
        root.addWidget(diagnostic)
        root.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.set_simple_mode(state.simple_mode)

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    @Slot()
    def _login(self) -> None:
        address = self.address.text().strip()
        password = self.password.text()
        if not address or not password:
            QMessageBox.warning(self, "Brak danych", "Wpisz adres e-mail oraz hasło do poczty.")
            return
        self.login_requested.emit(address, password)

    @Slot()
    @Slot(bool)
    @Slot(str)
    def _appearance(self, _unused: Any = None) -> None:
        scale = self.FONT_VALUES.get(self.font_size.currentText(), 100)
        self.appearance_changed.emit(scale, self.contrast.isChecked())

    @Slot(int)
    def _mode_changed(self, _index: int) -> None:
        self.mode_changed.emit(bool(self.mode_combo.currentData()))

    def set_simple_mode(self, simple: bool) -> None:
        index = self.mode_combo.findData(bool(simple))
        self.mode_combo.blockSignals(True)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        self.mode_combo.blockSignals(False)
        self.ollama_card.setVisible(not simple)
        self.devices.setVisible(not simple)
        self.backup_card.setVisible(not simple)
        self.diagnostic_card.setVisible(not simple)

    @Slot(bool)
    def _toggle_diagnostics(self, visible: bool) -> None:
        self.diagnostics.setVisible(visible)
        self.diagnostic_toggle.setText("Ukryj informacje techniczne" if visible else "Pokaż informacje techniczne")

    @Slot()
    def _choose_backup_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Wybierz katalog kopii", self.backup_directory.text())
        if directory:
            self.backup_directory.setText(directory)
            self._backup_configuration()

    @Slot()
    @Slot(bool)
    @Slot(int)
    def _backup_configuration(self, _unused: Any = None) -> None:
        self.backup_configuration_changed.emit(
            self.backup_directory.text(), self.automatic_backup.isChecked(), self.backup_keep.value()
        )

    def set_backup_busy(self, busy: bool, text: str = "") -> None:
        self.backup_button.setEnabled(not busy)
        self.restore_button.setEnabled(not busy)
        restoring = any(value in text.lower() for value in ("przywrac", "sprawdzanie integralności"))
        self.backup_button.setText("Tworzenie kopii…" if busy and not restoring else "Utwórz kopię zapasową")
        self.restore_button.setText("Przywracanie danych…" if busy and restoring else "Przywróć dane z kopii")
        if text:
            self.backup_status.setText(text)

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.login_button.setEnabled(not busy)
        self.login_button.setText("Łączenie z pocztą…" if busy else "Połącz i pobierz wiadomości")
        if text:
            self.account_status.setText(text)


class MainWindow(QMainWindow):
    connect_mail = Signal(str, str)
    load_mail_details = Signal(str)
    save_mail_attachment = Signal(str, int, str)
    prepare_mail_attachment = Signal(str, int)
    prepare_indexed_attachment = Signal(object)
    disconnect_mail = Signal()
    prepare_reply_context = Signal(str, str)
    check_sent_message = Signal(str)
    append_sent_message = Signal(bytes, str)
    load_mailbox_page = Signal(str, int, int, str)
    refresh_mailbox_page = Signal(str, int, str)
    load_semantic_mail_details = Signal(str)
    load_semantic_mail_page = Signal(str, int, int)
    load_document_mail_page = Signal(str, int, int)
    open_indexed_mail = Signal(object)

    PROTECTED_PAGES = {0, 1, 3, 4, 5, 6, 10}
    LOGIN_REQUIRED_PAGE = 7
    HOME_PAGE = 8
    WIZARD_PAGE = 9
    SEMANTIC_PAGE = 10
    HELP_PAGE = 11

    def __init__(self, state: AppSettings | None = None, mail_service: MailService | None = None) -> None:
        super().__init__()
        ensure_readable_font()
        self.state = state or AppSettings()
        self.account_address = self.state.address
        self._busy_mail = False
        self._mail_connected = False
        self._analysis_running = False
        self._reply_running = False
        self._backup_running = False
        self._backup_manual = False
        self._restore_started = False
        self._session_password = ""
        self._pending_password = ""
        self._pending_address = ""
        self._refresh_running = False
        self._refresh_retry_used = False
        self._reconnecting_for_refresh = False
        self._refresh_request: tuple[str, int, str] | None = None
        self._wizard_active = False
        self._reply_uid = ""
        self._reply_identity = ""
        self._last_sent_task: dict[str, Any] | None = None
        self.draft_store = DraftStore()
        self.document_index = DocumentIndex()
        self.semantic_index = SemanticMailIndex()
        self._semantic_queue: list[str] = []
        self._semantic_queued: set[str] = set()
        self._semantic_current_uid = ""
        self._semantic_cancelled = False
        self._semantic_scan_queue: list[tuple[str, int]] = []
        self._semantic_scan_waiting = False
        self._semantic_full_scan = False
        self._semantic_expected = 0
        self._semantic_search_running = False
        self._semantic_disabled_reason = ""
        self._document_scan_active = False
        self._document_scan_complete = False
        self._document_scan_folders: list[dict[str, Any]] = []
        self._document_scan_folder_index = 0
        self._document_scan_offset = 0
        self._document_scan_seen = 0
        self._document_search_pending: tuple[str, str, int, int, bool] | None = None
        self.backup_manager = BackupManager(
            self.state.settings, self.document_index.path, self.draft_store.directory
        )
        self._startup_recovery: dict[str, Any] | None = None
        self._startup_recovery_error = ""
        try:
            self._startup_recovery = self.backup_manager.recover_interrupted_restore()
        except Exception as exc:
            self._startup_recovery_error = str(exc)
        self._pending_saved_attachment: tuple[str, int] | None = None
        self._pending_document_action = ""
        self._pending_document: dict[str, Any] | None = None
        self._pending_message_selection = ""
        self._message_return_page = 0
        self._reply_return_page = 0
        self._document_return_page = 4
        self._device_busy = False
        self._devices: dict[str, Any] = {"printers": [], "scanners": [], "scanner_notice": ""}
        self._pending_print_task: dict[str, Any] | None = None
        self._scan_dialog: ScanDialog | None = None
        self._scan_task: dict[str, Any] | None = None
        self._closing = False
        self._shutdown_pending = False
        self._allow_close = False
        self._sidebar_collapsed = self.state.sidebar_collapsed
        self.thread_pool = QThreadPool.globalInstance()
        # QRunnable nie ma rodzica QObject. Trzymamy silne referencje do
        # pracowników aż do sygnału finished, bo przy długiej analizie Python
        # mógł wcześniej usunąć obiekt sygnałów i wynik nie docierał do UI.
        self._active_workers: set[FunctionWorker] = set()
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._shutdown_timeout)
        self.setWindowTitle("Asystent Poczty")
        self.setMinimumSize(680, 520)
        self.resize(1280, 820)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QFrame()
        self.sidebar = sidebar
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        side = QVBoxLayout(sidebar)
        self.sidebar_layout = side
        side.setContentsMargins(18, 24, 18, 20)
        side.setSpacing(10)
        collapse_row = QHBoxLayout()
        collapse_row.addStretch(1)
        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setAccessibleName("Zwiń menu boczne")
        self.sidebar_toggle.setToolTip("Zwiń menu boczne")
        self.sidebar_toggle.setStyleSheet(
            "QToolButton { color: white; font-size: 22px; padding: 5px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.16); border-radius: 8px; }"
        )
        self.sidebar_toggle.clicked.connect(self._toggle_sidebar)
        collapse_row.addWidget(self.sidebar_toggle)
        side.addLayout(collapse_row)
        brand = QLabel("Asystent\nPoczty")
        self.sidebar_brand = brand
        brand.setObjectName("AppTitle")
        side.addWidget(brand)
        subtitle = QLabel("Prosty plan z wiadomości")
        self.sidebar_subtitle = subtitle
        subtitle.setStyleSheet("color: #dbe9ff; background: transparent;")
        subtitle.setWordWrap(True)
        side.addWidget(subtitle)
        side.addSpacing(18)
        main_label = QLabel("GŁÓWNE")
        self.sidebar_main_label = main_label
        main_label.setStyleSheet("color: #bcd3f3; background: transparent; font-weight: 700;")
        side.addWidget(main_label)
        self.nav_buttons: list[QPushButton] = []
        self.nav_by_index: dict[int, QPushButton] = {}
        navigation = (
            (self.HOME_PAGE, "Start", QStyle.StandardPixmap.SP_DesktopIcon),
            (0, "Poczta", QStyle.StandardPixmap.SP_DirOpenIcon),
            (1, "Moje zadania", QStyle.StandardPixmap.SP_DialogApplyButton),
            (4, "Moje dokumenty", QStyle.StandardPixmap.SP_FileIcon),
            (self.SEMANTIC_PAGE, "Szukaj", QStyle.StandardPixmap.SP_FileDialogContentsView),
            (self.HELP_PAGE, "Pomoc", QStyle.StandardPixmap.SP_DialogHelpButton),
        )
        for index, text, icon in navigation:
            button = QPushButton(text)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setProperty("pageIndex", index)
            button.setIcon(self.style().standardIcon(icon))
            button.setIconSize(QSize(22, 22))
            button.setAccessibleName(text)
            button.setProperty("fullText", text)
            button.clicked.connect(partial(self.show_page, index))
            side.addWidget(button)
            self.nav_buttons.append(button)
            self.nav_by_index[index] = button
        side.addStretch(1)
        settings_button = QPushButton("Ustawienia")
        settings_button.setObjectName("NavButton")
        settings_button.setCheckable(True)
        settings_button.setProperty("pageIndex", 2)
        settings_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        settings_button.setIconSize(QSize(22, 22))
        settings_button.clicked.connect(partial(self.show_page, 2))
        settings_button.setAccessibleName("Ustawienia")
        settings_button.setProperty("fullText", "Ustawienia")
        side.addWidget(settings_button)
        self.nav_buttons.append(settings_button)
        self.nav_by_index[2] = settings_button
        privacy = QLabel("Analiza działa lokalnie.\nWysyłka wymaga potwierdzenia.")
        self.sidebar_privacy = privacy
        privacy.setStyleSheet("color: #dbe9ff; background: transparent;")
        privacy.setWordWrap(True)
        side.addWidget(privacy)
        root.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.mail_page = MailPage()
        self.tasks_page = TasksPage()
        self.reply_page = ReplyPage()
        self.settings_page = SettingsPage(self.state)
        self.documents_page = DocumentsPage()
        self.message_page = MessagePage()
        self.document_preview_page = DocumentPreviewPage()
        self.login_required_page = LoginRequiredPage()
        self.home_page = HomePage()
        self.first_run_wizard = FirstRunWizard()
        self.semantic_page = SemanticSearchPage()
        self.help_page = HelpPage()
        self.stack.addWidget(self.mail_page)
        self.stack.addWidget(self.tasks_page)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.reply_page)
        self.stack.addWidget(self.documents_page)
        self.stack.addWidget(self.message_page)
        self.stack.addWidget(self.document_preview_page)
        self.stack.addWidget(self.login_required_page)
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.first_run_wizard)
        self.stack.addWidget(self.semantic_page)
        self.stack.addWidget(self.help_page)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._apply_sidebar_state(persist=False)
        self.statusBar().showMessage("Gotowe")

        self.mail_thread = QThread(self)
        self.mail_service = mail_service or MailService()
        self.mail_service.moveToThread(self.mail_thread)
        self.connect_mail.connect(self.mail_service.connect_and_load)
        self.load_mail_details.connect(self.mail_service.load_details)
        self.save_mail_attachment.connect(self.mail_service.save_attachment)
        self.prepare_mail_attachment.connect(self.mail_service.prepare_attachment)
        self.prepare_indexed_attachment.connect(self.mail_service.prepare_indexed_attachment)
        self.prepare_reply_context.connect(self.mail_service.prepare_reply)
        self.check_sent_message.connect(self.mail_service.check_sent)
        self.append_sent_message.connect(self.mail_service.save_sent_copy)
        self.load_mailbox_page.connect(self.mail_service.load_mailbox)
        self.refresh_mailbox_page.connect(self.mail_service.refresh_mailbox)
        self.load_semantic_mail_details.connect(self.mail_service.load_semantic_details)
        self.load_semantic_mail_page.connect(self.mail_service.load_semantic_page)
        self.load_document_mail_page.connect(self.mail_service.load_document_page)
        self.open_indexed_mail.connect(self.mail_service.open_indexed_message)
        self.disconnect_mail.connect(self.mail_service.disconnect)
        self.mail_service.connected.connect(self._messages_loaded)
        self.mail_service.folders_ready.connect(self.mail_page.set_folders)
        self.mail_service.folders_ready.connect(self.semantic_page.set_folders)
        self.mail_service.mailbox_ready.connect(self._mailbox_ready)
        self.mail_service.mailbox_refreshed.connect(self._mailbox_refreshed)
        self.mail_service.refresh_failed.connect(self._mailbox_refresh_failed)
        self.mail_service.details_ready.connect(self._mail_details_ready)
        self.mail_service.attachment_saved.connect(self._attachment_saved)
        self.mail_service.attachment_prepared.connect(self._attachment_prepared)
        self.mail_service.failed.connect(self._operation_failed)
        self.mail_service.busy_changed.connect(self._mail_busy_changed)
        self.mail_service.disconnected.connect(self._mail_disconnected)
        self.mail_service.diagnostic.connect(self._append_diagnostic)
        self.mail_service.reply_context_ready.connect(self._reply_context_ready)
        self.mail_service.sent_check_ready.connect(self._sent_check_ready)
        self.mail_service.sent_copy_ready.connect(self._sent_copy_ready)
        self.mail_service.semantic_details_ready.connect(self._semantic_details_ready)
        self.mail_service.semantic_details_failed.connect(self._semantic_details_failed)
        self.mail_service.semantic_page_ready.connect(self._semantic_page_ready)
        self.mail_service.semantic_page_failed.connect(self._semantic_page_failed)
        self.mail_service.document_page_ready.connect(self._document_scan_page_ready)
        self.mail_service.document_page_failed.connect(self._document_scan_failed)
        self.mail_service.indexed_message_ready.connect(self._indexed_message_ready)
        self.mail_thread.finished.connect(self._shutdown_finished)
        self.mail_thread.start()

        self.settings_page.login_requested.connect(self._login)
        self.settings_page.ollama_check_requested.connect(self._check_ollama)
        self.settings_page.appearance_changed.connect(self._set_appearance)
        self.settings_page.mode_changed.connect(self._set_simple_mode)
        self.settings_page.wizard_requested.connect(self._start_first_run_wizard)
        self.settings_page.backup_requested.connect(lambda: self._start_backup(True))
        self.settings_page.restore_requested.connect(self._choose_restore_backup)
        self.settings_page.backup_configuration_changed.connect(self._save_backup_configuration)
        self.mail_page.message_selected.connect(self._open_message)
        self.mail_page.mailbox_requested.connect(self.load_mailbox_page)
        self.mail_page.refresh_requested.connect(self._refresh_mailbox)
        self.mail_page.help_requested.connect(lambda: self._show_context_help("mail"))
        self.mail_page.semantic_requested.connect(lambda: self.show_page(self.SEMANTIC_PAGE))
        self.login_required_page.login_requested.connect(self._go_to_login)
        self.home_page.mail_requested.connect(lambda: self.show_page(0))
        self.home_page.tasks_requested.connect(lambda: self.show_page(1))
        self.home_page.documents_requested.connect(lambda: self.show_page(4))
        self.home_page.compose_requested.connect(self._start_new_message)
        self.home_page.continue_requested.connect(self._continue_last_task)
        self.home_page.settings_requested.connect(lambda: self.show_page(2))
        self.home_page.help_requested.connect(lambda: self.show_page(self.HELP_PAGE))
        self.first_run_wizard.mail_check_requested.connect(self._wizard_check_mail)
        self.first_run_wizard.ai_check_requested.connect(self._wizard_check_ai)
        self.first_run_wizard.devices_check_requested.connect(self._wizard_check_devices)
        self.first_run_wizard.finished_requested.connect(self._finish_first_run_wizard)
        self.message_page.back_requested.connect(self._return_from_message)
        self.document_preview_page.back_requested.connect(self._close_document_preview)
        self.document_preview_page.print_requested.connect(self._document_print)
        self.document_preview_page.save_copy_requested.connect(self._document_save_copy)
        self.document_preview_page.message_requested.connect(self._document_message)
        self.message_page.analyze_requested.connect(self._analyze)
        self.message_page.save_attachment_requested.connect(self._choose_attachment_path)
        self.message_page.reply_requested.connect(self._prepare_reply)
        self.message_page.help_requested.connect(lambda: self._show_context_help("message"))
        self.message_page.tasks_requested.connect(lambda: self.show_page(1))
        self.tasks_page.progress_changed.connect(self._save_progress)
        self.tasks_page.device_action_requested.connect(self._task_device_action)
        self.tasks_page.reply_action_requested.connect(self._prepare_reply_from_tasks)
        self.tasks_page.source_requested.connect(self._open_message)
        self.reply_page.draft_changed.connect(self._save_draft)
        self.reply_page.save_requested.connect(self._save_draft_explicit)
        self.reply_page.ai_requested.connect(self._generate_reply)
        self.reply_page.review_requested.connect(self._review_reply)
        self.reply_page.sent_check_requested.connect(self._check_sent)
        self.reply_page.delete_requested.connect(self._delete_draft)
        self.reply_page.help_requested.connect(lambda: self._show_context_help("reply"))
        self.reply_page.back_requested.connect(self._return_from_reply)
        self.settings_page.devices.refresh_requested.connect(self._refresh_devices)
        self.settings_page.devices.print_file_requested.connect(self._choose_pdf_to_print)
        self.settings_page.devices.test_print_requested.connect(self._print_test_page)
        self.settings_page.devices.scan_requested.connect(self._open_scan_dialog)
        self.settings_page.devices.import_scan_requested.connect(self._import_scanned_pdf)
        self.documents_page.search_requested.connect(self._search_documents)
        self.documents_page.server_preview_requested.connect(self._document_preview)
        self.documents_page.print_requested.connect(self._document_print)
        self.documents_page.save_copy_requested.connect(self._document_save_copy)
        self.documents_page.message_requested.connect(self._document_message)
        self.documents_page.task_requested.connect(self._document_task)
        self.documents_page.favorite_requested.connect(self._document_favorite)
        self.documents_page.rebuild_requested.connect(self._repair_document_index)
        self.documents_page.ocr_requested.connect(self._document_ocr)
        self.documents_page.help_requested.connect(lambda: self._show_context_help("documents"))
        self.semantic_page.back_requested.connect(lambda: self.show_page(0))
        self.semantic_page.search_requested.connect(self._semantic_search)
        self.semantic_page.prepare_requested.connect(self._prepare_semantic_index)
        self.semantic_page.cancel_requested.connect(self._cancel_semantic_index)
        self.semantic_page.rebuild_requested.connect(self._rebuild_semantic_index)
        self.semantic_page.open_requested.connect(self._open_semantic_result)
        self.help_page.mail_requested.connect(lambda: self.show_page(0))
        self.help_page.tasks_requested.connect(lambda: self.show_page(1))
        self.help_page.documents_requested.connect(lambda: self.show_page(4))
        self.help_page.search_requested.connect(lambda: self.show_page(self.SEMANTIC_PAGE))
        self.help_page.settings_requested.connect(lambda: self.show_page(2))
        # Samo uruchomienie tylko odczytuje wygląd. Nie zapisujemy ponownie
        # niezmienionych ustawień, dzięki czemu chwilowo zajęty plik ustawień
        # nie może zablokować startu aplikacji.
        self._apply_appearance(self.state.font_scale, self.state.high_contrast)
        self._set_simple_mode(self.state.simple_mode, persist=False)
        self._reset_account_views()
        self._update_home_summary()
        if self.state.first_run_complete:
            self.show_page(self.HOME_PAGE)
        else:
            self._start_first_run_wizard()
        # W trybie bez ekranu (automatyczne testy) nie pytamy prawdziwego WIA
        # o sprzęt. Na zwykłym pulpicie wykrywanie nadal startuje od razu.
        if QApplication.platformName().lower() != "offscreen":
            QTimer.singleShot(0, self._refresh_devices)
            QTimer.singleShot(1000, self._show_startup_recovery)
            QTimer.singleShot(5000, self._automatic_backup_if_due)

    @Slot(int)
    def show_page(self, index: int) -> None:
        if index in self.PROTECTED_PAGES and not self._mail_connected:
            self.login_required_page.set_target(index)
            self.stack.setCurrentIndex(self.LOGIN_REQUIRED_PAGE)
            self._select_navigation(index)
            return
        self.stack.setCurrentIndex(index)
        self._select_navigation(index)
        if index == 4:
            self._search_documents(self.documents_page.query.text(), self.documents_page.category())
        elif index == self.HOME_PAGE:
            self._update_home_summary()

    def _select_navigation(self, page_index: int) -> None:
        """Zaznacza sekcję nadrzędną także na ekranach szczegółów."""
        section = {
            5: 0,  # szczegół wiadomości należy do Poczty
            6: 4,  # podgląd PDF należy do Dokumentów
            3: 0,  # edytor jest czynnością rozpoczętą z Poczty
            self.LOGIN_REQUIRED_PAGE: 2,
        }.get(page_index, page_index)
        for button in self.nav_buttons:
            button.setChecked(int(button.property("pageIndex")) == section)

    @Slot()
    def _return_from_message(self) -> None:
        self.show_page(self._message_return_page)

    @Slot()
    def _return_from_reply(self) -> None:
        self.show_page(self._reply_return_page)

    @Slot()
    def _go_to_login(self) -> None:
        self.stack.setCurrentIndex(2)
        for button in self.nav_buttons:
            button.setChecked(int(button.property("pageIndex")) == 2)
        target = self.settings_page.password if self.settings_page.address.text().strip() else self.settings_page.address
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _reset_account_views(self) -> None:
        """Czyści wyłącznie widoki; zapisane postępy i szkice pozostają na dysku."""
        self.mail_page.reset()
        self.message_page.reset()
        self.tasks_page.reset()
        self.documents_page.reset()
        self.document_preview_page.reset()
        self.reply_page.reset()
        self.semantic_page.reset()
        self._semantic_queue.clear()
        self._semantic_queued.clear()
        self._semantic_scan_queue.clear()
        self._semantic_scan_waiting = False
        self._semantic_full_scan = False
        self._semantic_cancelled = True
        self._document_scan_active = False
        self._document_scan_complete = False
        self._document_scan_folders.clear()
        self._document_scan_folder_index = 0
        self._document_scan_offset = 0
        self._document_scan_seen = 0
        self._document_search_pending = None
        self._reply_uid = ""
        self._reply_identity = ""
        self._pending_message_selection = ""
        if hasattr(self, "home_page"):
            self._update_home_summary()

    def _update_home_summary(self) -> None:
        if not hasattr(self, "home_page"):
            return
        unread = sum(1 for row in self.mail_page.all_rows if row.get("unread")) if self._mail_connected else None
        tasks = self.tasks_page.analysis.get("tasks") if isinstance(self.tasks_page.analysis.get("tasks"), list) else []
        message_identity = self.tasks_page.uid
        if not tasks and self._mail_connected:
            saved_plan = self.state.dashboard_plan(self.account_address)
            tasks = saved_plan.get("tasks") if isinstance(saved_plan.get("tasks"), list) else []
            message_identity = str(saved_plan.get("message_identity") or "")
        completed_saved = self.state.completed_tasks(self.account_address, message_identity) if message_identity else set()
        remaining: list[dict[str, Any]] = []
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                continue
            key = str(task.get("key") or stable_task_key(task, index + 1))
            checkbox = self.tasks_page.checkboxes.get(key)
            if checkbox is not None:
                done = checkbox.isChecked()
            else:
                done = key in completed_saved
            if not done:
                remaining.append(task)
        deadlines = sorted(str(task.get("deadline")) for task in remaining if task.get("deadline"))
        nearest = format_polish_datetime(deadlines[0]) if deadlines else ""
        last = str(remaining[0].get("action") or "") if remaining else ""
        self.home_page.set_summary(self._mail_connected, unread, len(remaining), nearest, last)

    @Slot()
    def _continue_last_task(self) -> None:
        if self.tasks_page.analysis.get("tasks"):
            self.show_page(1)
            return
        plan = self.state.dashboard_plan(self.account_address)
        mail_uid = str(plan.get("mail_uid") or "")
        if mail_uid and self.mail_page.select_message(mail_uid):
            self.message_page.status.setText("Otwórz analizę wiadomości, aby kontynuować plan krok po kroku.")
            return
        self.show_page(0)
        self.statusBar().showMessage("Otwórz ostatnią wiadomość i wybierz „Przeanalizuj wiadomość”", 6000)

    def _show_context_help(self, topic: str) -> None:
        help_text = {
            "home": (
                "Wybierz jedną dużą czynność. „Sprawdź pocztę” otwiera wiadomości, „Moje zadania” pokazuje plan, "
                "„Znajdź dokument” wyszukuje PDF-y, a „Napisz wiadomość” otwiera pusty formularz."
            ),
            "mail": (
                "Aby otworzyć wiadomość, wybierz ją raz z listy. Użyj pola Szukaj, jeśli wiadomość jest starsza. "
                "Przycisk „Odśwież pocztę” sprawdza, czy przyszły nowe wiadomości."
            ),
            "message": (
                "Przeczytaj wiadomość i sprawdź listę załączników. „Przeanalizuj wiadomość” przygotuje plan krok po kroku. "
                "„Zapisz wybrany PDF” zapisuje załącznik, a „Przygotuj odpowiedź” otwiera bezpieczny edytor."
            ),
            "documents": (
                "Wpisz nazwę dokumentu, osobę albo datę i wybierz „Szukaj w dokumentach”. Po wybraniu pliku możesz "
                "otworzyć PDF, zapisać kopię lub przejść do powiązanej wiadomości. Drukowanie zawsze wymaga Twojej decyzji."
            ),
            "reply": (
                "Sprawdź kolejno odbiorcę, temat, treść i załączniki. Przycisk „Sprawdź przed wysłaniem” pokaże pełne "
                "podsumowanie. Wiadomość zostanie wysłana dopiero po dodatkowym, wyraźnym zatwierdzeniu."
            ),
        }
        QMessageBox.information(self, "Pomoc do tego ekranu", help_text.get(topic, help_text["home"]))

    @Slot()
    def _start_first_run_wizard(self) -> None:
        self._wizard_active = True
        self.first_run_wizard.reset(
            self.state.address,
            self.state.font_scale,
            self.state.high_contrast,
            self.state.simple_mode,
        )
        if self._mail_connected:
            self.first_run_wizard.set_mail_result(True, "Konto jest już połączone z WP Pocztą.")
        self.sidebar.hide()
        self.stack.setCurrentIndex(self.WIZARD_PAGE)
        for button in self.nav_buttons:
            button.setChecked(False)

    @Slot(str, str)
    def _wizard_check_mail(self, address: str, password: str) -> None:
        self._login(address, password)

    @Slot()
    def _wizard_check_ai(self) -> None:
        worker = FunctionWorker(check_ollama_analysis)
        worker.signals.result.connect(self._wizard_ai_ready)
        worker.signals.failed.connect(self._wizard_ai_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Kreator AI: {text}"))
        self._start_worker(worker)

    @Slot(object)
    def _wizard_ai_ready(self, result: object) -> None:
        version = result.get("version", "uruchomiona") if isinstance(result, dict) else "uruchomiona"
        self.first_run_wizard.set_ai_result(True, f"Asystent AI działa. Ollama: {version}, model: {MODEL_NAME}.")

    @Slot(str)
    def _wizard_ai_failed(self, message: str) -> None:
        self.first_run_wizard.set_ai_result(False, message)
        self._append_diagnostic(f"Kreator AI: {message}")

    @Slot()
    def _wizard_check_devices(self) -> None:
        self._refresh_devices()

    @Slot(dict)
    def _finish_first_run_wizard(self, options: dict[str, Any]) -> None:
        self.state.default_printer = str(options.get("default_printer") or "")
        self.state.default_scanner = str(options.get("default_scanner") or "")
        self._set_appearance(int(options.get("font_scale") or 120), bool(options.get("high_contrast")))
        self._set_simple_mode(bool(options.get("simple_mode", True)))
        self.state.first_run_complete = True
        self._wizard_active = False
        self.sidebar.show()
        self.show_page(0)

    def _set_logged_out(self) -> None:
        self._mail_connected = False
        self._session_password = ""
        self._pending_password = ""
        self._pending_address = ""
        self._refresh_running = False
        self._refresh_retry_used = False
        self._reconnecting_for_refresh = False
        self._refresh_request = None
        self._reset_account_views()

    def _start_worker(self, worker: FunctionWorker) -> None:
        """Uruchamia pracownika i zachowuje jego sygnały do końca operacji."""
        self._active_workers.add(worker)
        worker.signals.finished.connect(partial(self._release_worker, worker))
        self.thread_pool.start(worker)

    def _release_worker(self, worker: FunctionWorker) -> None:
        self._active_workers.discard(worker)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._update_responsive_layout(event.size().width())
        super().resizeEvent(event)

    def _update_responsive_layout(self, width: int) -> None:
        if self._sidebar_collapsed:
            self.sidebar.setFixedWidth(72)
            return
        large_text = self.state.font_scale >= 120
        sidebar_width = 240 if width >= 1180 or large_text else 205 if width >= 900 else 190
        self.sidebar.setFixedWidth(sidebar_width)
        self.sidebar_subtitle.setVisible(width >= 980)
        self.sidebar_privacy.setVisible(width >= 980 and self.height() >= 700)
        self.sidebar_main_label.setVisible(self.height() >= 660)

    @Slot()
    def _toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        self._apply_sidebar_state(persist=True)

    def _apply_sidebar_state(self, persist: bool = True) -> None:
        """Zostawia same ikony albo przywraca pełne, opisane menu."""
        collapsed = bool(self._sidebar_collapsed)
        if persist:
            self.state.sidebar_collapsed = collapsed
        self.sidebar_toggle.setText("›" if collapsed else "‹  Zwiń")
        action = "Rozwiń menu boczne" if collapsed else "Zwiń menu boczne"
        self.sidebar_toggle.setAccessibleName(action)
        self.sidebar_toggle.setToolTip(action)
        self.sidebar_layout.setContentsMargins(
            8 if collapsed else 18,
            12 if collapsed else 24,
            8 if collapsed else 18,
            12 if collapsed else 20,
        )
        self.sidebar.setFixedWidth(72 if collapsed else 230)
        self.sidebar_brand.setText("AP" if collapsed else "Asystent\nPoczty")
        self.sidebar_brand.setAlignment(
            Qt.AlignmentFlag.AlignCenter if collapsed else Qt.AlignmentFlag.AlignLeft
        )
        self.sidebar_subtitle.setVisible(not collapsed and self.width() >= 980)
        self.sidebar_main_label.setVisible(not collapsed and self.height() >= 660)
        self.sidebar_privacy.setVisible(not collapsed and self.width() >= 980 and self.height() >= 700)
        for button in self.nav_buttons:
            full_text = str(button.property("fullText") or button.accessibleName())
            button.setText("" if collapsed else full_text)
            button.setToolTip(full_text if collapsed else "")
            button.setProperty("collapsed", collapsed)
            button.style().unpolish(button)
            button.style().polish(button)
        if not collapsed:
            self._update_responsive_layout(self.width())

    @Slot(bool)
    def _set_simple_mode(self, simple: bool, persist: bool = True) -> None:
        if persist:
            self.state.simple_mode = simple
        self.settings_page.set_simple_mode(simple)
        simple_pages = {self.HOME_PAGE, 0, 1, 4, self.SEMANTIC_PAGE, self.HELP_PAGE}
        for index, button in self.nav_by_index.items():
            button.setVisible(not simple or index in simple_pages)
        self.sidebar_subtitle.setText("Najważniejsze czynności" if simple else "Pełne funkcje aplikacji")
        self.statusBar().showMessage(
            "Włączono tryb prosty" if simple else "Włączono tryb zaawansowany",
            4000,
        )

    @Slot(str, str)
    def _login(self, address: str, password: str) -> None:
        if self._busy_mail:
            return
        self._mail_connected = False
        self._reset_account_views()
        self._pending_address = address
        self._pending_password = password
        self.settings_page.set_busy(True, "Łączenie z pocztą…")
        self.connect_mail.emit(address, password)

    @Slot(list)
    def _messages_loaded(self, messages: list[dict[str, Any]]) -> None:
        self._mail_connected = True
        if self._pending_address:
            self.account_address = self._pending_address
            self.state.address = self._pending_address
        if self._pending_password:
            self._session_password = self._pending_password
        self._pending_address = ""
        self._pending_password = ""
        self.settings_page.password.clear()
        self.settings_page.account_status.setText("Połączono z pocztą.")
        self.mail_page.refresh_button.setEnabled(True)
        self.mail_page.semantic_button.setEnabled(True)
        self._semantic_cancelled = False
        semantic_status = self.semantic_index.status(self.account_address)
        if semantic_status["indexed"]:
            self.semantic_page.status.setText(
                f"Możesz wyszukiwać w {semantic_status['indexed']} przygotowanych wiadomościach."
            )
        QTimer.singleShot(0, self._process_semantic_queue)
        if self._wizard_active:
            self.first_run_wizard.set_mail_result(True, "Połączenie z WP Pocztą działa prawidłowo.")
            self._update_home_summary()
            return
        if self._reconnecting_for_refresh and self._refresh_request is not None:
            self._reconnecting_for_refresh = False
            folder, limit, query = self._refresh_request
            self.refresh_mailbox_page.emit(folder, limit, query)
            return
        if not self.mail_page.all_rows:
            self.mail_page.set_messages(messages)
        self._update_home_summary()
        self.show_page(0)
        self.statusBar().showMessage("Poczta została pobrana", 5000)

    @Slot(object)
    def _mailbox_ready(self, page_value: object) -> None:
        if not isinstance(page_value, dict):
            return
        if not self._reconnecting_for_refresh:
            self.mail_page.set_mailbox(page_value)
        index_address = self._pending_address or self.account_address
        for row in page_value.get("rows", []):
            if isinstance(row, dict):
                try:
                    self.document_index.upsert_message(index_address, row)
                    names = row.get("attachment_names") if isinstance(row.get("attachment_names"), list) else []
                    if names:
                        self.document_index.index_received_attachments(index_address, row, names)
                except Exception as exc:
                    self._append_diagnostic(f"Indeks wiadomości: {type(exc).__name__}: {exc}")
        self._queue_semantic_rows(page_value.get("rows", []))
        if self._pending_message_selection:
            if self.mail_page.select_message(self._pending_message_selection):
                self._pending_message_selection = ""
        self._update_home_summary()

    @Slot(str, int, str)
    def _refresh_mailbox(self, folder: str, limit: int, query: str) -> None:
        if not self._mail_connected or self._busy_mail or self._refresh_running:
            return
        self._refresh_running = True
        self._refresh_retry_used = False
        self._refresh_request = (folder, limit, query)
        self.mail_page.set_busy(True, "Sprawdzanie nowych wiadomości…")
        self.refresh_mailbox_page.emit(folder, limit, query)

    @Slot(object)
    def _mailbox_refreshed(self, page_value: object) -> None:
        if not isinstance(page_value, dict):
            self._mailbox_refresh_failed("Serwer zwrócił nieprawidłową listę wiadomości.", False)
            return
        added = self.mail_page.set_refresh_result(page_value)
        for row in page_value.get("rows", []):
            if not isinstance(row, dict):
                continue
            try:
                self.document_index.upsert_message(self.account_address, row)
                names = row.get("attachment_names") if isinstance(row.get("attachment_names"), list) else []
                if names:
                    self.document_index.index_received_attachments(self.account_address, row, names)
            except Exception as exc:
                self._append_diagnostic(f"Indeks odświeżonej poczty: {type(exc).__name__}: {exc}")
        self._queue_semantic_rows(page_value.get("rows", []))
        self._refresh_running = False
        self._refresh_request = None
        self._reconnecting_for_refresh = False
        self.mail_page.set_busy(False)
        self._update_home_summary()
        if added == 1:
            text = "Dodano 1 nową wiadomość"
        elif 2 <= added <= 4:
            text = f"Dodano {added} nowe wiadomości"
        elif added:
            text = f"Dodano {added} nowych wiadomości"
        else:
            text = "Brak nowych wiadomości"
        self.statusBar().showMessage(text, 5000)

    @Slot(str, bool)
    def _mailbox_refresh_failed(self, message: str, connection_lost: bool = True) -> None:
        if not connection_lost:
            self._refresh_running = False
            self._refresh_request = None
            self.mail_page.set_busy(False)
            self.mail_page.status.setText("Nie udało się teraz odświeżyć poczty. Spróbuj ponownie.")
            self.statusBar().showMessage("Nie udało się odświeżyć poczty")
            self._append_diagnostic(f"Odświeżanie poczty: {message}")
            return
        if (
            not self._refresh_retry_used
            and self.account_address
            and self._session_password
        ):
            self._refresh_retry_used = True
            self._reconnecting_for_refresh = True
            self._pending_address = self.account_address
            self._pending_password = self._session_password
            self.mail_page.set_busy(True, "Połączenie przerwane. Ponowne łączenie…")
            self.statusBar().showMessage("Połączenie przerwane. Ponowne łączenie…")
            self.connect_mail.emit(self.account_address, self._session_password)
            return
        self._set_logged_out()
        self.settings_page.account_status.setText("Połączenie z pocztą zostało przerwane.")
        self.show_page(0)
        self.statusBar().showMessage("Połączenie z pocztą zostało przerwane")
        self._append_diagnostic(f"Połączenie z pocztą zostało przerwane: {message}")

    @Slot(str)
    def _open_message(self, uid: str) -> None:
        row = self.mail_page.rows.get(uid, {})
        if not row:
            return
        current_page = self.stack.currentIndex()
        if not self._pending_message_selection and current_page in {0, 1, 4, self.SEMANTIC_PAGE}:
            self._message_return_page = current_page
        body = self.mail_page.bodies_by_uid.get(uid, "")
        details_loaded = uid in self.mail_page.attachments_by_uid
        row_names = row.get("attachment_names") if isinstance(row.get("attachment_names"), list) else []
        attachments = self.mail_page.attachments_by_uid.get(uid, list(row_names))
        self.message_page.show_message(uid, row, body, attachments)
        self.message_page.set_has_tasks(
            self.tasks_page.mail_uid == uid and bool(self.tasks_page.analysis.get("tasks"))
        )
        self.show_page(5)
        # Sama treść mogła zostać wcześniej pobrana przez indeks wyszukiwania.
        # Załączniki pobieramy ponownie tylko wtedy, gdy nagłówek rzeczywiście
        # informuje o ich obecności. To usuwa zbędne żądania i spóźnione błędy.
        needs_attachments = bool(row.get("has_attachments") or row_names)
        if not body or (needs_attachments and not details_loaded):
            self.load_mail_details.emit(uid)

    @Slot(str, str, list)
    def _mail_details_ready(self, uid: str, body: str, attachments: list[str]) -> None:
        self.mail_page.set_details(uid, body, attachments)
        self.message_page.set_details(uid, body, attachments)
        row = self.mail_page.rows.get(uid, {})
        if not row:
            return
        try:
            self.document_index.upsert_message(self.account_address, row, body)
            self.document_index.index_received_attachments(self.account_address, row, attachments)
        except Exception as exc:
            self._append_diagnostic(f"Indeks dokumentów wiadomości: {type(exc).__name__}: {exc}")
        # Otwarta wiadomość ma już pełną treść, więc może od razu zasilić
        # wyszukiwanie po opisie bez ponownego pobierania z serwera.
        if uid != self._semantic_current_uid:
            self._start_semantic_embedding(uid, body)
        if self.stack.currentIndex() == 4:
            self._search_documents(self.documents_page.query.text(), self.documents_page.category())

    def _queue_semantic_rows(self, rows_value: object) -> None:
        """Dodaje wiadomości do stopniowego indeksowania, bez duplikatów."""
        rows = rows_value if isinstance(rows_value, list) else []
        for value in rows:
            if not isinstance(value, dict):
                continue
            row = dict(value)
            uid = str(row.get("uid") or "")
            if not uid:
                continue
            # Wynik ze starszego folderu można dzięki temu otworzyć od razu.
            self.mail_page.rows[uid] = row
            body = self.mail_page.bodies_by_uid.get(uid, "")
            if body:
                try:
                    if not self.semantic_index.needs_indexing(self.account_address, row, body):
                        continue
                except Exception as exc:
                    self._append_diagnostic(f"Kontrola indeksu znaczeniowego: {type(exc).__name__}: {exc}")
            if uid != self._semantic_current_uid and uid not in self._semantic_queued:
                self._semantic_queue.append(uid)
                self._semantic_queued.add(uid)
        QTimer.singleShot(0, self._process_semantic_queue)

    def _process_semantic_queue(self) -> None:
        if (
            not self._mail_connected or self._semantic_cancelled
            or self._semantic_current_uid or self._semantic_search_running
        ):
            return
        while self._semantic_queue:
            uid = self._semantic_queue.pop(0)
            self._semantic_queued.discard(uid)
            row = self.mail_page.rows.get(uid)
            if not row:
                continue
            body = self.mail_page.bodies_by_uid.get(uid, "")
            if body:
                self._start_semantic_embedding(uid, body)
            else:
                self._semantic_current_uid = uid
                self.load_semantic_mail_details.emit(uid)
            return
        if self._semantic_full_scan:
            self._request_next_semantic_page()

    def _start_semantic_embedding(self, uid: str, body: str) -> None:
        row = self.mail_page.rows.get(uid)
        if not row or not body or self._semantic_cancelled:
            if uid == self._semantic_current_uid:
                self._semantic_current_uid = ""
            return
        if self._semantic_current_uid and self._semantic_current_uid != uid:
            if uid not in self._semantic_queued:
                self._semantic_queue.insert(0, uid)
                self._semantic_queued.add(uid)
            return
        try:
            if not self.semantic_index.needs_indexing(self.account_address, row, body):
                self._semantic_current_uid = ""
                QTimer.singleShot(0, self._process_semantic_queue)
                return
        except Exception as exc:
            self._append_diagnostic(f"Kontrola indeksu znaczeniowego: {type(exc).__name__}: {exc}")
        self._semantic_current_uid = uid
        self.semantic_page.set_indexing(
            True,
            f"Przygotowywanie wyszukiwania… pozostało co najmniej {len(self._semantic_queue)} wiadomości.",
        )
        worker = FunctionWorker(
            self.semantic_index.upsert_message,
            self.account_address,
            dict(row),
            body,
            OllamaEmbedder(),
        )
        worker.signals.result.connect(lambda _value, item=uid: self._semantic_embedding_ready(item))
        worker.signals.failed.connect(lambda message, item=uid: self._semantic_embedding_failed(item, message))
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Indeks znaczeniowy: {text}"))
        worker.signals.finished.connect(lambda item=uid: self._semantic_embedding_finished(item))
        self._start_worker(worker)

    @Slot(str, str, list)
    def _semantic_details_ready(self, uid: str, body: str, attachments: list[str]) -> None:
        if uid != self._semantic_current_uid:
            return
        self.mail_page.set_details(uid, body, attachments)
        row = self.mail_page.rows.get(uid, {})
        if row:
            try:
                self.document_index.upsert_message(self.account_address, row, body)
                self.document_index.index_received_attachments(self.account_address, row, attachments)
            except Exception as exc:
                self._append_diagnostic(f"Indeks dokumentów wiadomości: {type(exc).__name__}: {exc}")
        self._start_semantic_embedding(uid, body)

    @Slot(str, str)
    def _semantic_details_failed(self, uid: str, message: str) -> None:
        if uid == self._semantic_current_uid:
            self._semantic_current_uid = ""
        self._append_diagnostic(f"Pominięto wiadomość w indeksie: {message}")
        QTimer.singleShot(0, self._process_semantic_queue)

    def _semantic_embedding_ready(self, uid: str) -> None:
        self._semantic_disabled_reason = ""
        status = self.semantic_index.status(self.account_address)
        self.semantic_page.status.setText(
            f"Przygotowano {status['indexed']} wiadomości do wyszukiwania po opisie."
        )

    def _semantic_embedding_failed(self, uid: str, message: str) -> None:
        self._semantic_disabled_reason = message
        self._semantic_queue.clear()
        self._semantic_queued.clear()
        self._semantic_scan_queue.clear()
        self._semantic_full_scan = False
        self.semantic_page.set_indexing(False)
        self.semantic_page.status.setText(
            f"Nie można teraz przygotować wyszukiwania po opisie. {message} "
            "Zwykłe wyszukiwanie poczty nadal działa."
        )

    def _semantic_embedding_finished(self, uid: str) -> None:
        if uid == self._semantic_current_uid:
            self._semantic_current_uid = ""
        if not self._semantic_queue and not self._semantic_full_scan:
            self.semantic_page.set_indexing(False)
        QTimer.singleShot(0, self._process_semantic_queue)
        if self._shutdown_pending:
            QTimer.singleShot(0, self._start_shutdown)

    @Slot()
    def _prepare_semantic_index(self) -> None:
        if not self._mail_connected or self._semantic_current_uid:
            return
        self._semantic_cancelled = False
        self._semantic_disabled_reason = ""
        self._semantic_full_scan = True
        self._semantic_expected = 0
        self.semantic_index.set_complete(self.account_address, False)
        folders = [
            identifier for identifier, item in self.mail_page.folders.items()
            if item.get("role") != "trash"
        ]
        self._semantic_scan_queue = [(folder, 0) for folder in folders]
        self.semantic_page.set_indexing(
            True, "Przygotowywanie wiadomości ze wszystkich folderów. Możesz w każdej chwili anulować."
        )
        self._process_semantic_queue()

    def _request_next_semantic_page(self) -> None:
        if self._semantic_scan_waiting or self._semantic_current_uid or self._semantic_queue:
            return
        if not self._semantic_scan_queue:
            self._semantic_full_scan = False
            self.semantic_index.set_complete(self.account_address, True, self._semantic_expected)
            self.semantic_page.set_indexing(False)
            status = self.semantic_index.status(self.account_address)
            self.semantic_page.status.setText(
                f"Wyszukiwanie jest gotowe. Przygotowano {status['indexed']} wiadomości."
            )
            return
        folder, offset = self._semantic_scan_queue.pop(0)
        self._semantic_scan_waiting = True
        self.load_semantic_mail_page.emit(folder, offset, 50)

    @Slot(object)
    def _semantic_page_ready(self, page_value: object) -> None:
        self._semantic_scan_waiting = False
        if not self._semantic_full_scan or not isinstance(page_value, dict):
            return
        rows = [row for row in page_value.get("rows", []) if isinstance(row, dict)]
        total = int(page_value.get("total") or 0)
        offset = int(page_value.get("offset") or 0)
        folder = str(page_value.get("folder") or "")
        self._semantic_expected += len(rows)
        if rows and offset + len(rows) < total:
            self._semantic_scan_queue.insert(0, (folder, offset + len(rows)))
        self._queue_semantic_rows(rows)
        if not rows:
            QTimer.singleShot(0, self._process_semantic_queue)

    @Slot(str)
    def _semantic_page_failed(self, message: str) -> None:
        self._semantic_scan_waiting = False
        self._append_diagnostic(f"Nie udało się przygotować części folderu: {message}")
        self.semantic_page.status.setText(
            "Nie udało się odczytać jednego z folderów. Pozostałe przygotowywanie będzie kontynuowane."
        )
        QTimer.singleShot(0, self._process_semantic_queue)

    @Slot()
    def _cancel_semantic_index(self) -> None:
        self._semantic_cancelled = True
        self._semantic_queue.clear()
        self._semantic_queued.clear()
        self._semantic_scan_queue.clear()
        self._semantic_full_scan = False
        self.semantic_index.set_complete(self.account_address, False, self._semantic_expected)
        self.semantic_page.set_indexing(False)
        self.semantic_page.status.setText(
            "Przygotowywanie zostało zatrzymane. Dotychczas przygotowane wiadomości nadal można wyszukiwać."
        )

    @Slot(str, str)
    def _semantic_search(self, query: str, folder: str) -> None:
        if self._semantic_search_running:
            return
        if len(query.strip()) < 3:
            self.semantic_page.status.setText("Opisz wiadomość co najmniej kilkoma słowami.")
            return
        self._semantic_search_running = True
        self.semantic_page.set_busy(True, "Szukanie wiadomości o podobnym znaczeniu…")
        worker = FunctionWorker(
            self.semantic_index.search,
            self.account_address,
            query,
            OllamaEmbedder(),
            folder,
            10,
            True,
            OllamaSearchVerifier(),
        )
        worker.signals.result.connect(self.semantic_page.show_results)
        worker.signals.failed.connect(self._semantic_search_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Wyszukiwanie po opisie: {text}"))
        worker.signals.finished.connect(self._semantic_search_finished)
        self._start_worker(worker)

    @Slot(str)
    def _semantic_search_failed(self, message: str) -> None:
        self.semantic_page.status.setText(
            f"Nie udało się wyszukać po opisie. {message} Zwykłe wyszukiwanie w Poczcie nadal działa."
        )

    @Slot()
    def _semantic_search_finished(self) -> None:
        self._semantic_search_running = False
        self.semantic_page.set_busy(False)
        QTimer.singleShot(0, self._process_semantic_queue)
        if self._shutdown_pending:
            QTimer.singleShot(0, self._start_shutdown)

    @Slot(object)
    def _open_semantic_result(self, record_value: object) -> None:
        record = dict(record_value) if isinstance(record_value, dict) else {}
        self._message_return_page = self.SEMANTIC_PAGE
        uid = str(record.get("uid") or record.get("mail_ref") or "")
        if uid and uid in self.mail_page.rows:
            self._open_message(uid)
            return
        self.open_indexed_mail.emit(record)

    @Slot(object, str, list)
    def _indexed_message_ready(self, row_value: object, body: str, attachments: list[str]) -> None:
        if not isinstance(row_value, dict):
            return
        row = dict(row_value)
        uid = str(row.get("uid") or "")
        if not uid:
            return
        self.mail_page.rows[uid] = row
        self.mail_page.set_details(uid, body, attachments)
        self.message_page.show_message(uid, row, body, attachments)
        self.show_page(5)

    @Slot()
    def _rebuild_semantic_index(self) -> None:
        answer = QMessageBox.question(
            self,
            "Odbudować wyszukiwanie?",
            "Zostanie usunięty wyłącznie lokalny indeks wyszukiwania. Wiadomości na poczcie nie zostaną usunięte.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._cancel_semantic_index()
        worker = FunctionWorker(self.semantic_index.rebuild, self.account_address)
        worker.signals.result.connect(lambda _value: self._prepare_semantic_index())
        worker.signals.failed.connect(self._semantic_search_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Odbudowa indeksu: {text}"))
        self._start_worker(worker)

    @Slot(bool, str)
    def _mail_busy_changed(self, busy: bool, text: str) -> None:
        self._busy_mail = busy
        account_text = text if busy else ("Połączono z pocztą." if self._mail_connected else "Konto nie jest połączone.")
        self.settings_page.set_busy(busy, account_text)
        self.mail_page.set_busy(busy, text)
        self.message_page.set_busy(busy, text)
        if text:
            self.statusBar().showMessage(text)
        elif not self._analysis_running:
            self.statusBar().showMessage("Gotowe")
        if self._shutdown_pending and not busy and not self._analysis_running:
            QTimer.singleShot(0, self._start_shutdown)

    @Slot(str, str)
    def _operation_failed(self, area: str, message: str) -> None:
        friendly = friendly_error(area, message)
        if area == "poczta" and self._wizard_active:
            self._set_logged_out()
            self.settings_page.password.clear()
            self.first_run_wizard.set_mail_result(False, friendly)
            self._append_diagnostic(f"Błąd kreatora poczty: {message}")
            return
        if area == "poczta":
            self._set_logged_out()
        if area == "załącznik":
            self._pending_print_task = None
            if self._pending_document_action:
                self._pending_document_action = ""
                self._pending_document = None
                self.documents_page.set_document_busy(False, friendly)
        if area == "kopia Wysłane":
            self.reply_page.status.setText(
                "Serwer przyjął wiadomość, ale nie udało się zapisać dodatkowej kopii w folderze Wysłane."
            )
            self._append_diagnostic(f"Błąd ({area}): {message}")
            return
        if area == "folder Wysłane":
            self.reply_page.status.setText(
                "Nie udało się teraz sprawdzić folderu Wysłane. Nie ponawiaj wysyłki, jeśli poprzedni status był niepewny."
            )
            QMessageBox.warning(
                self, "Nie udało się sprawdzić folderu Wysłane",
                f"{friendly}\n\nDla bezpieczeństwa aplikacja nie odblokowała ponownej wysyłki.",
            )
            self._append_diagnostic(f"Błąd ({area}): {message}")
            return
        if area == "poczta":
            self.settings_page.password.clear()
            # Błąd logowania pozostaje przy polach konta. Nie otwieramy nad
            # przebudowywanym widokiem dodatkowego modalnego okna Windows.
            self.show_page(2)
        if area == "folder poczty":
            self.mail_page.status.setText(friendly)
        elif area == "wiadomość":
            self.message_page.status.setText(friendly)
        self.settings_page.account_status.setText(friendly)
        self.statusBar().showMessage("Operacja nie powiodła się")
        self._append_diagnostic(f"Błąd ({area}): {message}")

    @Slot(str)
    def _attachment_saved(self, path: str) -> None:
        if self._pending_saved_attachment is not None:
            uid, index = self._pending_saved_attachment
            self._pending_saved_attachment = None
            document = self.document_index.find_attachment(uid, index)
            if document:
                try:
                    self.document_index.set_local_path(str(document.get("id") or ""), path)
                    self._index_pdf_background(str(document.get("id") or ""), path)
                except Exception as exc:
                    self._append_diagnostic(f"Indeks zapisanego załącznika: {type(exc).__name__}: {exc}")
        self.statusBar().showMessage("Załącznik został zapisany", 5000)
        QMessageBox.information(self, "Zapisano", f"Załącznik zapisano w:\n{path}")

    @Slot()
    def _refresh_devices(self) -> None:
        if self._device_busy:
            return
        self._device_busy = True
        self.settings_page.devices.set_busy(True)
        worker = FunctionWorker(discover_devices)
        worker.signals.result.connect(self._devices_ready)
        worker.signals.failed.connect(self._devices_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Urządzenia: {text}"))
        worker.signals.finished.connect(self._device_operation_finished)
        self._start_worker(worker)

    @Slot(object)
    def _devices_ready(self, result: object) -> None:
        if not isinstance(result, dict):
            self._devices_failed("Windows zwrócił nieprawidłową listę urządzeń.")
            return
        self._devices = result
        self.settings_page.devices.set_devices(result)
        if self._wizard_active:
            self.first_run_wizard.set_devices(result, self.state.default_printer, self.state.default_scanner)
        printers = list(result.get("printers") or [])
        scanners = list(result.get("scanners") or [])
        self._append_diagnostic(
            f"Urządzenia: {len(printers)} wpisów drukarek, {len(scanners)} skanerów WIA."
        )

    @Slot(str)
    def _devices_failed(self, message: str) -> None:
        self.settings_page.devices.setEnabled(True)
        self.settings_page.devices.status.setText(message)
        if self._wizard_active:
            self.first_run_wizard.set_devices_error(message)
        self._append_diagnostic(f"Wykrywanie urządzeń: {message}")

    @Slot()
    def _device_operation_finished(self) -> None:
        self._device_busy = False
        self.settings_page.devices.set_busy(False)
        if self._shutdown_pending and not self._busy_mail and not self._analysis_running:
            QTimer.singleShot(0, self._start_shutdown)

    @Slot(str, object)
    def _task_device_action(self, action: str, task_value: object) -> None:
        task = task_value if isinstance(task_value, dict) else {}
        if self._busy_mail or self._device_busy or self._pending_print_task is not None:
            self.statusBar().showMessage("Inna operacja urządzenia jeszcze trwa", 4000)
            return
        if action == "print":
            names = self.tasks_page.attachment_names
            pdf_rows = [index for index, name in enumerate(names) if str(name).lower().endswith(".pdf")]
            if not pdf_rows:
                QMessageBox.information(
                    self, "Brak PDF-u w wiadomości",
                    "To zadanie wymaga wydruku, ale w otwartej wiadomości nie ma załącznika PDF. "
                    "Możesz wybrać dokument zapisany na komputerze.",
                )
                self._choose_pdf_to_print(task)
                return
            row = pdf_rows[0]
            if len(pdf_rows) > 1:
                choices = [names[index] for index in pdf_rows]
                chosen, accepted = QInputDialog.getItem(
                    self, "Wybierz dokument", "Który załącznik chcesz wydrukować?", choices, 0, False
                )
                if not accepted:
                    return
                row = pdf_rows[choices.index(chosen)]
            self._pending_print_task = task
            self.prepare_mail_attachment.emit(self.tasks_page.mail_uid, row)
        elif action == "scan":
            self._open_scan_dialog(task)

    @Slot(str, int, str)
    def _attachment_prepared(self, _uid: str, _index: int, path: str) -> None:
        if self._pending_document_action:
            action = self._pending_document_action
            record = self._pending_document
            self._pending_document_action = ""
            self._pending_document = None
            self.documents_page.set_document_busy(False)
            if isinstance(record, dict) and record.get("id"):
                self._index_pdf_background(str(record.get("id")), path, ocr=(action == "ocr"))
            if action == "preview":
                self._show_document_preview(path, record if isinstance(record, dict) else {})
            elif action == "print":
                self._open_print_dialog(path, None)
            elif action == "ocr":
                self.show_page(4)
            return
        task = self._pending_print_task
        self._pending_print_task = None
        self._open_print_dialog(path, task)

    @Slot()
    def _choose_pdf_to_print(self, task: dict[str, Any] | None = None) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz PDF do wydruku", "", "Dokument PDF (*.pdf)")
        if path:
            self._open_print_dialog(path, task if isinstance(task, dict) else None)

    @Slot()
    def _print_test_page(self) -> None:
        try:
            path = create_test_pdf()
        except RuntimeError as exc:
            QMessageBox.warning(self, "Nie udało się przygotować testu", str(exc))
            return
        self._open_print_dialog(path, None, cleanup_after=True)

    def _open_print_dialog(
        self, path: str, task: dict[str, Any] | None = None, cleanup_after: bool = False
    ) -> None:
        if self._device_busy:
            QMessageBox.information(self, "Operacja trwa", "Poczekaj na zakończenie poprzedniej operacji urządzenia.")
            if cleanup_after:
                Path(path).unlink(missing_ok=True)
            return
        try:
            validate_pdf(path)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Nie można wydrukować dokumentu", str(exc))
            if cleanup_after:
                Path(path).unlink(missing_ok=True)
            return
        printers = list(self._devices.get("printers") or [])
        if not printers:
            QMessageBox.warning(
                self, "Brak drukarki",
                "Windows nie wykrył żadnej drukarki. Włącz urządzenie, sprawdź sterownik i odśwież listę w Ustawieniach.",
            )
            if cleanup_after:
                Path(path).unlink(missing_ok=True)
            return
        quantity = task.get("quantity") if isinstance(task, dict) else 1
        copies = quantity if isinstance(quantity, int) and quantity > 0 else 1
        dialog = PrintDialog(path, printers, self.state.default_printer, copies, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            if cleanup_after:
                Path(path).unlink(missing_ok=True)
            return
        options = dialog.options()
        selected = next(
            (item for item in printers if str(item.get("name")) == str(options.get("printer"))), {}
        )
        if str(selected.get("status")) in {"offline", "błąd"}:
            proceed = QMessageBox.question(
                self, "Drukarka nie jest gotowa",
                "Windows zgłasza, że ta drukarka jest offline albo ma błąd. Dokument może utknąć w kolejce. "
                "Czy mimo to przekazać go do drukowania?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                if cleanup_after:
                    Path(path).unlink(missing_ok=True)
                return
        self.state.default_printer = str(options.get("printer") or "")
        self._device_busy = True
        self.statusBar().showMessage("Przekazywanie dokumentu do kolejki drukowania…")
        worker = FunctionWorker(print_pdf, path, options)
        worker.signals.result.connect(partial(self._print_queued, task))
        worker.signals.failed.connect(self._print_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Wydruk: {text}"))
        if cleanup_after:
            worker.signals.finished.connect(lambda: Path(path).unlink(missing_ok=True))
        worker.signals.finished.connect(self._device_operation_finished)
        self._start_worker(worker)

    @Slot(object, object)
    def _print_queued(self, task: dict[str, Any] | None, result: object) -> None:
        data = result if isinstance(result, dict) else {}
        printer = str(data.get("printer") or "drukarki")
        pages = ", ".join(str(value) for value in data.get("pages", []))
        answer = QMessageBox.question(
            self, "Dokument przekazany do drukarki",
            f"Dokument został przekazany do kolejki „{printer}” (strony: {pages or 'wszystkie'}).\n\n"
            "Czy kartki zostały faktycznie wydrukowane prawidłowo?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes and task:
            self._complete_task(task)
        elif answer == QMessageBox.StandardButton.No:
            QMessageBox.information(
                self, "Zadanie pozostaje niewykonane",
                "Krok nie został odhaczony. Sprawdź papier, tusz, połączenie i kolejkę drukowania, a potem spróbuj ponownie.",
            )
        self.statusBar().showMessage("Dokument przekazano do kolejki drukowania", 5000)

    @Slot(str)
    def _print_failed(self, message: str) -> None:
        QMessageBox.warning(
            self, "Nie udało się przekazać wydruku",
            f"{message}\n\nZadanie nie zostało oznaczone jako wykonane.",
        )
        self._append_diagnostic(f"Wydruk nieudany: {message}")

    @Slot()
    def _open_scan_dialog(self, task: dict[str, Any] | None = None) -> None:
        if self._device_busy:
            QMessageBox.information(self, "Operacja trwa", "Poczekaj na zakończenie poprzedniej operacji urządzenia.")
            return
        scanners = list(self._devices.get("scanners") or [])
        if not scanners:
            answer = QMessageBox.question(
                self, "Skaner nie jest dostępny",
                "Windows WIA nie wykrył skanera. Możesz zeskanować dokument programem producenta, "
                "a następnie wczytać gotowy PDF. Czy chcesz teraz wybrać taki plik?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._import_scanned_pdf(task)
            return
        dialog = ScanDialog(scanners, self.state.default_scanner, self)
        self._scan_dialog = dialog
        self._scan_task = task if isinstance(task, dict) else None
        dialog.scan_page_requested.connect(self._scan_page)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            path, _ = QFileDialog.getSaveFileName(self, "Zapisz zeskanowany dokument", "skan.pdf", "Dokument PDF (*.pdf)")
            if path:
                if not path.lower().endswith(".pdf"):
                    path += ".pdf"
                self._save_scan_pdf(list(dialog.pages), path, self._scan_task)
        self._scan_dialog = None
        self._scan_task = None

    @Slot(str, str)
    def _scan_page(self, device_id: str, source: str) -> None:
        if self._device_busy:
            return
        self._device_busy = True
        worker = FunctionWorker(scan_wia_pages, device_id, source)
        worker.signals.result.connect(self._scan_page_ready)
        worker.signals.failed.connect(self._scan_page_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Skanowanie: {text}"))
        worker.signals.finished.connect(self._device_operation_finished)
        self._start_worker(worker)

    @Slot(object)
    def _scan_page_ready(self, result: object) -> None:
        if self._scan_dialog is not None:
            pages = result if isinstance(result, list) else [result]
            for path in pages:
                if isinstance(path, str):
                    self._scan_dialog.add_page(path)

    @Slot(str)
    def _scan_page_failed(self, message: str) -> None:
        if self._scan_dialog is not None:
            self._scan_dialog.scan_failed(message)
        self._append_diagnostic(f"Skanowanie nieudane: {message}")

    def _save_scan_pdf(self, pages: list[str], path: str, task: dict[str, Any] | None) -> None:
        self._device_busy = True
        worker = FunctionWorker(images_to_pdf, pages, path)
        worker.signals.result.connect(partial(self._scan_pdf_ready, task))
        worker.signals.failed.connect(self._scan_pdf_failed)
        worker.signals.finished.connect(partial(self._cleanup_scan_pages, pages))
        worker.signals.finished.connect(self._device_operation_finished)
        self._start_worker(worker)

    def _cleanup_scan_pages(self, pages: list[str]) -> None:
        for value in pages:
            candidate = Path(value)
            if candidate.name.startswith("mailassistant_scan_"):
                candidate.unlink(missing_ok=True)

    @Slot(object, object)
    def _scan_pdf_ready(self, task: dict[str, Any] | None, result: object) -> None:
        data = result if isinstance(result, dict) else {}
        path = str(data.get("path") or "")
        answer = QMessageBox.question(
            self, "Skan zapisany",
            f"Utworzono PDF z {data.get('pages', 0)} stronami:\n{path}\n\nCzy podgląd dokumentu jest kompletny i czytelny?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            if path and self.tasks_page.uid:
                self.state.save_scanned_document(self.account_address, self.tasks_page.uid, path, True)
                self._index_scanned_document(path, task)
            if task:
                self._complete_task(task)
        self.statusBar().showMessage("Skan został zapisany jako PDF", 5000)

    @Slot(str)
    def _scan_pdf_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Nie udało się zapisać skanu", message)

    @Slot()
    def _import_scanned_pdf(self, task: dict[str, Any] | None = None) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz gotowy skan PDF", "", "Dokument PDF (*.pdf)")
        if not path:
            return
        try:
            _target, pages = validate_pdf(path)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Nieprawidłowy skan", str(exc))
            return
        answer = QMessageBox.question(
            self, "Gotowy skan",
            f"Dokument ma {pages} stron. Czy sprawdziłeś, że jest kompletny, czytelny i zawiera wymagane podpisy?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes and isinstance(task, dict):
            if self.tasks_page.uid:
                self.state.save_scanned_document(self.account_address, self.tasks_page.uid, path, True)
                self._index_scanned_document(path, task)
            self._complete_task(task)
        elif answer == QMessageBox.StandardButton.Yes and self.tasks_page.uid:
            self.state.save_scanned_document(self.account_address, self.tasks_page.uid, path, True)
            self._index_scanned_document(path, None)

    def _index_scanned_document(self, path: str, task: dict[str, Any] | None) -> None:
        task_key = ""
        task_label = ""
        if isinstance(task, dict):
            tasks = self.tasks_page.analysis.get("tasks") if isinstance(self.tasks_page.analysis.get("tasks"), list) else []
            for index, candidate in enumerate(tasks):
                if candidate is task or candidate == task:
                    task_key = stable_task_key(candidate, index + 1)
                    task_label = str(candidate.get("action") or "Zeskanowany dokument")
                    break
        try:
            document_id = self.document_index.add_local_document(
                self.account_address, path, "scanned", self.tasks_page.uid,
                task_key, task_label,
            )
            self._index_pdf_background(document_id, path)
        except Exception as exc:
            self._append_diagnostic(f"Indeks skanu: {type(exc).__name__}: {exc}")

    def _complete_task(self, task: dict[str, Any]) -> None:
        tasks = self.tasks_page.analysis.get("tasks") if isinstance(self.tasks_page.analysis.get("tasks"), list) else []
        for index, candidate in enumerate(tasks):
            if candidate is task or candidate == task:
                key = stable_task_key(candidate, index + 1)
                checkbox = self.tasks_page.checkboxes.get(key)
                if checkbox is not None:
                    checkbox.setChecked(True)
                return

    @Slot(str, int)
    def _choose_attachment_path(self, uid: str, index: int) -> None:
        names = self.mail_page.attachments_by_uid.get(uid, self.message_page.attachment_names)
        name = names[index] if index < len(names) else "zalacznik.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz załącznik PDF", name, "Dokument PDF (*.pdf)")
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self._pending_saved_attachment = (uid, index)
            self.save_mail_attachment.emit(uid, index, path)

    @Slot(str, str, str)
    def _analyze(self, uid: str, subject: str, body: str) -> None:
        if self._analysis_running:
            return
        self._analysis_running = True
        self.message_page.set_analysis_busy(True)
        self.message_page.status.setText("Ollama przygotowuje prosty plan działania…")
        self.statusBar().showMessage("Analiza wiadomości…")
        worker = create_analysis_worker(subject, body)
        worker.signals.result.connect(partial(self._analysis_ready, uid))
        worker.signals.failed.connect(self._analysis_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Analiza: {text}"))
        worker.signals.finished.connect(self._analysis_finished)
        self._start_worker(worker)

    @Slot(str, object)
    def _analysis_ready(self, uid: str, result: object) -> None:
        if self._shutdown_pending:
            return
        if not isinstance(result, dict):
            self._analysis_failed("Ollama zwróciła wynik w nieobsługiwanym formacie.")
            return
        technical_notes = result.get("_technical_notes")
        repair_count = len(technical_notes) if isinstance(technical_notes, list) else 0
        if repair_count:
            self._append_diagnostic(f"Analiza: walidator wykonał {repair_count} bezpiecznych korekt wyniku modelu.")
        row = self.mail_page.rows.get(uid, {})
        progress_id = str(row.get("progress_id") or uid)
        completed = self.state.completed_tasks(self.account_address, progress_id)
        source = f"{row.get('subject', '(brak tematu)')} — {row.get('sender', '(brak nadawcy)')}"
        self.tasks_page.set_analysis(
            progress_id, result, completed, source,
            list(self.mail_page.attachments_by_uid.get(uid, [])), uid,
        )
        self.message_page.set_has_tasks(bool(result.get("tasks")))
        dashboard_tasks = [
            {
                "key": stable_task_key(task, index + 1),
                "id": str(task.get("id") or f"krok-{index + 1}"),
                "action": str(task.get("action") or f"Krok {index + 1}"),
                "deadline": str(task.get("deadline") or ""),
            }
            for index, task in enumerate(result.get("tasks", [])) if isinstance(task, dict)
        ] if isinstance(result.get("tasks"), list) else []
        self.state.save_dashboard_plan(self.account_address, {
            "message_identity": progress_id,
            "mail_uid": uid,
            "subject": str(row.get("subject") or ""),
            "tasks": dashboard_tasks,
        })
        task_links = [
            (stable_task_key(task, index + 1), str(task.get("action") or f"Krok {index + 1}"))
            for index, task in enumerate(result.get("tasks", [])) if isinstance(task, dict)
        ] if isinstance(result.get("tasks"), list) else []
        try:
            self.document_index.link_message_tasks(progress_id, task_links)
        except Exception as exc:
            self._append_diagnostic(f"Powiązania dokumentów i zadań: {type(exc).__name__}: {exc}")
        self.show_page(1)
        self._update_home_summary()
        self.statusBar().showMessage("Plan działania jest gotowy", 5000)

    @Slot(str)
    def _analysis_failed(self, message: str) -> None:
        self._append_diagnostic(f"Błąd analizy: {message}")
        QMessageBox.warning(
            self,
            "Nie udało się przygotować planu",
            f"{message}\n\nTreść wiadomości nie została zmieniona. Możesz spróbować ponownie.",
        )

    @Slot()
    def _analysis_finished(self) -> None:
        self._analysis_running = False
        self.message_page.set_analysis_busy(False)
        self.message_page.status.setText("Wiadomość jest gotowa do analizy.")
        self.message_page.set_actions_enabled(not self._busy_mail)
        if not self._busy_mail:
            self.statusBar().showMessage("Gotowe")
        if self._shutdown_pending and not self._busy_mail:
            QTimer.singleShot(0, self._start_shutdown)

    @Slot(str, list)
    def _save_progress(self, message_identity: str, completed: list[str]) -> None:
        if message_identity:
            self.state.save_completed_tasks(self.account_address, message_identity, completed)
        self._update_home_summary()

    @Slot()
    def _start_new_message(self) -> None:
        if not self._mail_connected:
            self.show_page(3)
            return
        self._reply_return_page = self.stack.currentIndex()
        identity = "new:" + datetime.now().isoformat(timespec="microseconds")
        context = {
            "is_new": True,
            "to": "",
            "subject": "",
            "body": "",
            "attachments": [],
            "original_subject": "",
            "original_body": "",
            "original_recipient": "",
            "message_identity": identity,
        }
        context["message_id"] = ensure_message_id(context, self.account_address)
        self._reply_uid = ""
        self._reply_identity = identity
        self._last_sent_task = None
        self.reply_page.set_context(context, context)
        self._save_draft(context)
        self.show_page(3)

    @Slot(str)
    def _prepare_reply(self, uid: str) -> None:
        if not self._mail_connected:
            QMessageBox.information(self, "Konto nie jest połączone", "Najpierw połącz konto w Ustawieniach.")
            return
        if not uid:
            return
        self._reply_return_page = self.stack.currentIndex()
        self._reply_uid = uid
        row = self.mail_page.rows.get(uid, {})
        self._reply_identity = str(row.get("progress_id") or uid)
        self._last_sent_task = None
        self.prepare_reply_context.emit(uid, self.account_address)

    @Slot(object)
    def _prepare_reply_from_tasks(self, task_value: object) -> None:
        self._last_sent_task = task_value if isinstance(task_value, dict) else None
        uid = self.tasks_page.mail_uid or self.mail_page.current_uid
        if uid:
            self._prepare_reply(uid)
            # _prepare_reply czyści wybrane zadanie, dlatego odtwarzamy je.
            self._last_sent_task = task_value if isinstance(task_value, dict) else None

    @Slot(str, object)
    def _reply_context_ready(self, uid: str, context_value: object) -> None:
        if uid != self._reply_uid or not isinstance(context_value, dict):
            return
        context = dict(context_value)
        row = self.mail_page.rows.get(uid, {})
        identity = str(row.get("progress_id") or uid)
        self._reply_identity = identity
        context.update({
            "mail_uid": uid,
            "message_identity": identity,
            "original_body": self.mail_page.bodies_by_uid.get(uid, ""),
            "original_subject": str(row.get("subject") or context.get("original_subject") or ""),
        })
        try:
            draft = self.draft_store.load(self.account_address, identity)
        except Exception as exc:
            self._append_diagnostic(f"Szkic: {type(exc).__name__}: {exc}")
            draft = None
            QMessageBox.warning(self, "Nie udało się odczytać szkicu", str(exc))
        if draft is None:
            draft = dict(context)
            draft["body"] = ""
            draft["attachments"] = []
        if not draft.get("message_id"):
            draft["message_id"] = ensure_message_id(draft, self.account_address)
        self.reply_page.set_context(context, draft)
        self._save_draft(draft)
        status = self.state.send_status(self.account_address, identity)
        same_attempt = status.get("message_id") == draft.get("message_id")
        if same_attempt and status.get("status") == "sent":
            self.reply_page.status.setText("Ta odpowiedź została już przyjęta przez serwer pocztowy.")
        elif same_attempt and status.get("status") in {"sending", "uncertain"}:
            self.reply_page.status.setText(
                "Status poprzedniej próby jest niepewny. Najpierw użyj „Sprawdź folder Wysłane”."
            )
        self.show_page(3)
        QTimer.singleShot(0, self._offer_scanned_document)

    @Slot(dict)
    def _save_draft(self, draft: dict[str, Any]) -> None:
        if not self._reply_identity:
            return
        try:
            self.draft_store.save(self.account_address, self._reply_identity, draft)
        except Exception as exc:
            self.reply_page.status.setText(str(exc))
            self._append_diagnostic(f"Zapis szkicu: {type(exc).__name__}: {exc}")

    @Slot(dict)
    def _save_draft_explicit(self, draft: dict[str, Any]) -> None:
        self._save_draft(draft)
        self.reply_page.status.setText("Wersja robocza została bezpiecznie zapisana na tym komputerze.")

    @Slot()
    def _offer_scanned_document(self) -> None:
        if not self._reply_identity:
            return
        attached = {
            str(item.get("path") or "").lower()
            for item in self.reply_page.draft().get("attachments", []) if isinstance(item, dict)
        }
        scans = [
            item for item in self.state.scanned_documents(self.account_address, self._reply_identity)
            if Path(str(item.get("path") or "")).is_file()
            and str(item.get("path") or "").lower() not in attached
        ]
        if not scans:
            return
        chosen = scans[0]
        if len(scans) > 1:
            labels = [Path(str(item.get("path") or "")).name for item in scans]
            name, accepted = QInputDialog.getItem(
                self, "Wybierz zeskanowany dokument",
                "Który zeskanowany PDF dotyczy tej odpowiedzi?", labels, 0, False,
            )
            if not accepted:
                return
            chosen = scans[labels.index(name)]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Zeskanowany formularz jest gotowy")
        box.setText(
            f"Znaleziono dokument „{Path(str(chosen.get('path') or '')).name}”. Czy chcesz dołączyć go do odpowiedzi?"
        )
        attach = box.addButton("Tak, dołącz dokument", QMessageBox.ButtonRole.AcceptRole)
        choose = box.addButton("Wybierz inny dokument", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Nie dołączaj", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is attach:
            self.reply_page.add_attachment(str(chosen.get("path") or ""), approved=True)
        elif box.clickedButton() is choose:
            path, _ = QFileDialog.getOpenFileName(self, "Wybierz zeskanowany PDF", "", "Dokument PDF (*.pdf)")
            if path:
                self.reply_page.add_attachment(path, approved=False)

    @Slot()
    def _delete_draft(self) -> None:
        if not self._reply_identity or not confirm_delete_draft(self):
            return
        try:
            self.draft_store.delete(self.account_address, self._reply_identity)
        except Exception as exc:
            QMessageBox.warning(self, "Nie udało się usunąć szkicu", str(exc))
            return
        context = dict(self.reply_page.context)
        context.update({"body": "", "attachments": [], "message_id": ensure_message_id({}, self.account_address)})
        self.reply_page.set_context(self.reply_page.context, context)
        self.reply_page.status.setText("Wersja robocza została usunięta.")

    @Slot()
    def _generate_reply(self) -> None:
        if self._reply_running:
            return
        draft = self.reply_page.draft()
        completed: list[str] = []
        tasks = self.tasks_page.analysis.get("tasks") if isinstance(self.tasks_page.analysis.get("tasks"), list) else []
        for index, task in enumerate(tasks):
            key = stable_task_key(task, index + 1)
            box = self.tasks_page.checkboxes.get(key)
            if box is not None and box.isChecked():
                completed.append(str(task.get("action") or f"Krok {index + 1}"))
        try:
            attachments = validate_attachments(list(draft.get("attachments") or []))
        except Exception as exc:
            QMessageBox.warning(self, "Nieprawidłowy załącznik", str(exc))
            return
        names = [str(item.get("name") or "") for item in attachments]
        self._reply_running = True
        self.reply_page.set_busy(True, "Lokalna Ollama przygotowuje propozycję odpowiedzi…")
        worker = FunctionWorker(
            generate_reply,
            str(draft.get("original_subject") or ""),
            str(draft.get("original_body") or ""),
            completed,
            names,
        )
        worker.signals.result.connect(lambda value: self.reply_page.set_generated_body(str(value)))
        worker.signals.failed.connect(self._reply_generation_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Odpowiedź Ollamy: {text}"))
        worker.signals.finished.connect(self._reply_operation_finished)
        self._start_worker(worker)

    @Slot(str)
    def _reply_generation_failed(self, message: str) -> None:
        self.reply_page.status.setText(message)
        QMessageBox.warning(self, "Nie udało się przygotować propozycji", f"{message}\n\nMożesz napisać odpowiedź ręcznie.")

    @Slot()
    def _reply_operation_finished(self) -> None:
        self._reply_running = False
        self.reply_page.set_busy(False)
        if self._shutdown_pending and not self._busy_mail and not self._analysis_running:
            QTimer.singleShot(0, self._start_shutdown)

    @Slot()
    def _review_reply(self) -> None:
        if self._reply_running or not self._reply_identity:
            return
        draft = self.reply_page.draft()
        draft["message_id"] = ensure_message_id(draft, self.account_address)
        self.reply_page.context["message_id"] = draft["message_id"]
        self._save_draft(draft)
        status = self.state.send_status(self.account_address, self._reply_identity)
        same_attempt = status.get("message_id") == draft.get("message_id")
        if same_attempt and status.get("status") == "sent":
            QMessageBox.information(self, "Wiadomość już wysłana", "Ta odpowiedź została już przyjęta przez serwer pocztowy.")
            return
        if same_attempt and status.get("status") in {"sending", "uncertain"}:
            QMessageBox.warning(
                self, "Niepewny status",
                "Poprzednia próba mogła zostać przyjęta przez serwer. Aby uniknąć duplikatu, najpierw sprawdź folder Wysłane.",
            )
            return
        deadlines = [
            str(task.get("deadline") or "") for task in self.tasks_page.analysis.get("tasks", [])
            if isinstance(task, dict) and task.get("deadline")
        ] if isinstance(self.tasks_page.analysis.get("tasks"), list) else []
        warnings = preflight_warnings(draft, deadlines)
        task_rows = self.tasks_page.analysis.get("tasks") if isinstance(self.tasks_page.analysis.get("tasks"), list) else []
        requires_document = any(
            isinstance(task, dict)
            and any(word in (str(task.get("action") or "") + " " + str(task.get("source_quote") or "")).lower()
                    for word in ("odesłać", "przesłać", "wysłać skan", "dołączyć", "podpisany skan"))
            for task in task_rows
        )
        if requires_document and not draft.get("attachments"):
            warnings.append({
                "level": "blocker",
                "text": "Najpierw przygotuj zeskanowany dokument albo wybierz gotowy plik PDF.",
            })
        dialog = ReviewDialog(draft, warnings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._send_reply(draft)

    def _send_reply(self, draft: dict[str, Any]) -> None:
        if self._reply_running:
            self.reply_page.status.setText("Wysyłanie już trwa. Poczekaj na wynik serwera.")
            return
        if not self._session_password:
            QMessageBox.warning(
                self, "Sesja pocztowa wygasła",
                "Połącz konto ponownie w Ustawieniach. Hasło jest przechowywane tylko w pamięci bieżącej sesji.",
            )
            self.show_page(2)
            return
        message_id = str(draft.get("message_id") or ensure_message_id(draft, self.account_address))
        draft["message_id"] = message_id
        self.state.save_send_status(self.account_address, self._reply_identity, "sending", message_id)
        self._save_draft(draft)
        self._reply_running = True
        self.reply_page.set_busy(True, "Bezpieczne wysyłanie przez serwer WP…")
        worker = FunctionWorker(send_reply_safe, draft, self.account_address, self._session_password)
        worker.signals.result.connect(self._send_result)
        worker.signals.failed.connect(self._send_worker_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"SMTP: {text}"))
        worker.signals.finished.connect(self._reply_operation_finished)
        self._start_worker(worker)

    @Slot(object)
    def _send_result(self, result_value: object) -> None:
        result = result_value if isinstance(result_value, dict) else {"status": "failed", "message": "Nieprawidłowy wynik wysyłki."}
        status = str(result.get("status") or "failed")
        message_id = str(result.get("message_id") or self.reply_page.draft().get("message_id") or "")
        if status == "sent":
            self.state.save_send_status(self.account_address, self._reply_identity, "sent", message_id)
            sent_draft = self.reply_page.draft()
            for attachment in sent_draft.get("attachments", []):
                if not isinstance(attachment, dict):
                    continue
                path = str(attachment.get("path") or "")
                if Path(path).is_file():
                    try:
                        document_id = self.document_index.add_local_document(
                            self.account_address, path, "sent", self._reply_identity,
                            metadata={
                                "recipient": sent_draft.get("to"),
                                "subject": sent_draft.get("subject"),
                                "document_date": datetime.now().isoformat(timespec="minutes"),
                            },
                        )
                        self._index_pdf_background(document_id, path)
                    except Exception as exc:
                        self._append_diagnostic(f"Indeks wysłanego dokumentu: {type(exc).__name__}: {exc}")
            try:
                self.draft_store.delete(self.account_address, self._reply_identity)
            except Exception as exc:
                self._append_diagnostic(f"Usuwanie wysłanego szkicu: {type(exc).__name__}: {exc}")
            if self._last_sent_task:
                self._complete_task(self._last_sent_task)
            self.reply_page.status.setText("Wiadomość została przyjęta przez serwer pocztowy do wysłania.")
            QMessageBox.information(
                self, "Serwer przyjął wiadomość",
                "Wiadomość została przyjęta przez serwer pocztowy do wysłania. Nie oznacza to potwierdzenia doręczenia ani przeczytania.",
            )
            raw = result.get("raw")
            if isinstance(raw, bytes) and message_id:
                self.append_sent_message.emit(raw, message_id)
        elif status == "uncertain":
            self.state.save_send_status(self.account_address, self._reply_identity, "uncertain", message_id)
            message = str(result.get("message") or "Status wysyłki jest niepewny.")
            self.reply_page.status.setText(message)
            QMessageBox.warning(self, "Niepewny status wysyłki", message)
        else:
            self.state.save_send_status(self.account_address, self._reply_identity, "failed", message_id)
            message = str(result.get("message") or "Wiadomość nie została wysłana.")
            self.reply_page.status.setText(message)
            QMessageBox.warning(self, "Nie wysłano wiadomości", message)

    @Slot(str)
    def _send_worker_failed(self, message: str) -> None:
        draft = self.reply_page.draft()
        message_id = str(draft.get("message_id") or "")
        self.state.save_send_status(self.account_address, self._reply_identity, "failed", message_id)
        QMessageBox.warning(self, "Nie wysłano wiadomości", message)

    @Slot()
    def _check_sent(self) -> None:
        draft = self.reply_page.draft()
        message_id = str(draft.get("message_id") or "")
        if not message_id:
            QMessageBox.information(self, "Brak identyfikatora", "Ta wersja robocza nie była jeszcze wysyłana.")
            return
        self.check_sent_message.emit(message_id)

    @Slot(str, bool)
    def _sent_check_ready(self, message_id: str, found: bool) -> None:
        current = str(self.reply_page.draft().get("message_id") or "")
        if current != message_id:
            return
        if found:
            self.state.save_send_status(self.account_address, self._reply_identity, "sent", message_id)
            self.reply_page.status.setText("Wiadomość znajduje się w folderze Wysłane. Ponowna wysyłka jest zablokowana.")
            QMessageBox.information(self, "Wiadomość znaleziona", "Ta wiadomość znajduje się już w folderze Wysłane.")
            return
        answer = QMessageBox.question(
            self, "Nie znaleziono wiadomości",
            "Nie znaleziono tego identyfikatora w folderze Wysłane. Czy odblokować możliwość ponownej, ręcznie zatwierdzonej próby?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.state.save_send_status(self.account_address, self._reply_identity, "failed", message_id)
            self.reply_page.status.setText("Ponowna próba została odblokowana. Jeszcze raz sprawdź całą odpowiedź.")

    @Slot(str, object)
    def _sent_copy_ready(self, message_id: str, result_value: object) -> None:
        result = result_value if isinstance(result_value, dict) else {}
        note = "Kopia wiadomości jest widoczna w folderze Wysłane."
        if result.get("already_present"):
            note = "Serwer miał już kopię wiadomości w folderze Wysłane. Nie utworzono duplikatu."
        self.reply_page.status.setText(note)
        self._append_diagnostic(f"Kopia Wysłane ({message_id}): {note}")

    @Slot(str, str, int, int, bool)
    def _search_documents(
        self,
        query: str,
        category: str,
        offset: int = 0,
        limit: int = 25,
        whole_mailbox: bool = True,
    ) -> None:
        if not self._mail_connected:
            self.documents_page.set_search_busy(False)
            return
        request = (
            str(query or "").strip(), str(category or "all"), max(0, int(offset)),
            max(1, min(100, int(limit))), bool(whole_mailbox),
        )
        self._document_search_pending = request
        if whole_mailbox and not self._document_scan_complete:
            if not self._document_scan_active:
                self._start_document_scan()
            return
        self._run_document_search(request)

    def _run_document_search(self, request: tuple[str, str, int, int, bool]) -> None:
        query, category, offset, limit, _whole_mailbox = request
        self.documents_page.set_search_busy(True, "Wyszukiwanie w przygotowanej bibliotece dokumentów…")
        worker = FunctionWorker(
            self.document_index.search_page,
            self.account_address, query, category, offset, limit,
        )
        worker.signals.result.connect(self._document_rows_ready)
        worker.signals.failed.connect(lambda message: self.documents_page.status.setText(message))
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Biblioteka: {text}"))
        worker.signals.finished.connect(self._document_search_finished)
        self._start_worker(worker)

    @Slot(object)
    def _document_rows_ready(self, result: object) -> None:
        if not self._mail_connected:
            return
        if isinstance(result, dict):
            self.documents_page.set_page(result)
        else:
            rows = result if isinstance(result, list) else []
            self.documents_page.set_rows([dict(item) for item in rows if isinstance(item, dict)])

    @Slot()
    def _document_search_finished(self) -> None:
        if not self._document_scan_active:
            self.documents_page.set_search_busy(False)

    def _start_document_scan(self) -> None:
        folders = [
            dict(folder) for identifier, folder in self.mail_page.folders.items()
            if identifier != "__ALL__" and folder.get("role") != "trash"
        ]
        self._document_scan_folders = folders
        self._document_scan_folder_index = 0
        self._document_scan_offset = 0
        self._document_scan_seen = 0
        self._document_scan_active = True
        self.documents_page.set_search_busy(
            True,
            "Przygotowywanie dokumentów z całej skrzynki. Pobierane są tylko nagłówki i nazwy załączników…",
        )
        self._request_next_document_scan_page()

    def _request_next_document_scan_page(self) -> None:
        if not self._document_scan_active:
            return
        if self._document_scan_folder_index >= len(self._document_scan_folders):
            self._document_scan_active = False
            self._document_scan_complete = True
            pending = self._document_search_pending
            self.documents_page.status.setText(
                f"Przygotowano dokumenty z całej skrzynki. Sprawdzono {self._document_scan_seen} wiadomości."
            )
            if pending is not None:
                self._run_document_search(pending)
            else:
                self.documents_page.set_search_busy(False)
            return
        folder = self._document_scan_folders[self._document_scan_folder_index]
        folder_id = str(folder.get("id") or "INBOX")
        folder_name = str(folder.get("name") or folder_id)
        self.documents_page.set_search_busy(
            True,
            f"Przygotowywanie całej skrzynki: {folder_name}, wiadomości od {self._document_scan_offset + 1}…",
        )
        self.load_document_mail_page.emit(folder_id, self._document_scan_offset, 100)

    @Slot(object)
    def _document_scan_page_ready(self, value: object) -> None:
        if not self._document_scan_active or not isinstance(value, dict):
            return
        page = dict(value)
        rows = [dict(row) for row in page.get("rows", []) if isinstance(row, dict)]
        worker = FunctionWorker(self._index_document_scan_rows, self.account_address, rows)
        worker.signals.result.connect(lambda _result, current=page: self._document_scan_batch_indexed(current))
        worker.signals.failed.connect(self._document_scan_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Indeks dokumentów: {text}"))
        self._start_worker(worker)

    def _index_document_scan_rows(self, account: str, rows: list[dict[str, Any]]) -> int:
        result = self.document_index.index_mail_headers(account, rows)
        return int(result.get("documents") or 0)

    def _document_scan_batch_indexed(self, page: dict[str, Any]) -> None:
        if not self._document_scan_active:
            return
        rows = [row for row in page.get("rows", []) if isinstance(row, dict)]
        total = max(0, int(page.get("total") or 0))
        offset = max(0, int(page.get("offset") or self._document_scan_offset))
        self._document_scan_seen += len(rows)
        next_offset = offset + len(rows)
        if rows and next_offset < total:
            self._document_scan_offset = next_offset
        else:
            self._document_scan_folder_index += 1
            self._document_scan_offset = 0
        QTimer.singleShot(0, self._request_next_document_scan_page)

    @Slot(str)
    def _document_scan_failed(self, message: str) -> None:
        if not self._document_scan_active:
            return
        self._document_scan_active = False
        self._document_scan_complete = False
        self._append_diagnostic(f"Przygotowywanie dokumentów: {message}")
        self.documents_page.status.setText(
            "Nie udało się przygotować całej skrzynki. Pokazuję dokumenty, które były już wcześniej dostępne."
        )
        pending = self._document_search_pending
        if pending is not None:
            query, category, offset, limit, _whole = pending
            self._run_document_search((query, category, offset, limit, False))
        else:
            self.documents_page.set_search_busy(False)

    def _prepare_server_document(self, record: dict[str, Any], action: str) -> None:
        index = int(record.get("attachment_index", -1))
        if not record.get("folder") or not record.get("server_uid") or index < 0:
            QMessageBox.warning(self, "Dokument niedostępny", "Brakuje powiązania z wiadomością na serwerze.")
            return
        self._pending_document_action = action
        self._pending_document = dict(record)
        self.documents_page.set_document_busy(True, "Pobieranie dokumentu z serwera poczty…")
        self.prepare_indexed_attachment.emit(dict(record))

    @Slot(object)
    def _document_preview(self, value: object) -> None:
        record = value if isinstance(value, dict) else {}
        local = Path(str(record.get("local_path") or ""))
        if local.is_file():
            self._show_document_preview(str(local), record)
        else:
            self._prepare_server_document(record, "preview")

    def _show_document_preview(self, path: str, record: dict[str, Any]) -> None:
        name = str(record.get("name") or Path(path).name or "Dokument PDF")
        if self.document_preview_page.show_pdf(path, name, record):
            self._document_return_page = self.stack.currentIndex()
            self.documents_page.status.setText("Podgląd dokumentu jest gotowy.")
            self.show_page(6)
            return
        self.documents_page.status.setText("Nie udało się otworzyć podglądu tego PDF-u.")
        QMessageBox.warning(
            self,
            "Nie można otworzyć PDF-u",
            "Dokument został pobrany, ale nie można wyświetlić jego stron. Plik może być uszkodzony albo zabezpieczony hasłem.",
        )

    @Slot()
    def _close_document_preview(self) -> None:
        self.document_preview_page.close_pdf()
        self.show_page(self._document_return_page)

    @Slot(object)
    def _document_print(self, value: object) -> None:
        record = value if isinstance(value, dict) else {}
        local = Path(str(record.get("local_path") or ""))
        if local.is_file():
            self._open_print_dialog(str(local), None)
        else:
            self._prepare_server_document(record, "print")

    @Slot(object)
    def _document_save_copy(self, value: object) -> None:
        record = value if isinstance(value, dict) else {}
        name = str(record.get("name") or "dokument.pdf")
        target, _ = QFileDialog.getSaveFileName(self, "Zapisz kopię dokumentu", name, "Dokument PDF (*.pdf)")
        if not target:
            return
        if not target.lower().endswith(".pdf"):
            target += ".pdf"
        local = Path(str(record.get("local_path") or ""))
        if local.is_file():
            worker = FunctionWorker(shutil.copy2, str(local), target)
            worker.signals.result.connect(lambda _result: QMessageBox.information(self, "Zapisano", f"Kopię zapisano w:\n{target}"))
            worker.signals.failed.connect(lambda message: QMessageBox.warning(self, "Nie udało się zapisać kopii", message))
            self._start_worker(worker)
            return
        mail_ref = str(record.get("mail_ref") or "")
        index = int(record.get("attachment_index", -1))
        if mail_ref and index >= 0:
            self._pending_saved_attachment = (mail_ref, index)
            self.save_mail_attachment.emit(mail_ref, index, target)

    @Slot(object)
    def _document_message(self, value: object) -> None:
        record = value if isinstance(value, dict) else {}
        folder = str(record.get("folder") or "")
        mail_ref = str(record.get("mail_ref") or "")
        if not folder or not mail_ref:
            QMessageBox.information(self, "Brak wiadomości", "Ten lokalny dokument nie ma powiązanej wiadomości na serwerze.")
            return
        self._message_return_page = 4
        self._pending_message_selection = mail_ref
        self.show_page(0)
        if self.mail_page.select_message(mail_ref):
            self._pending_message_selection = ""
        else:
            self.load_mailbox_page.emit(folder, 0, self.mail_page.page_limit, "")

    @Slot(object)
    def _document_task(self, value: object) -> None:
        record = value if isinstance(value, dict) else {}
        if not record.get("task_labels"):
            QMessageBox.information(self, "Brak zadania", "Ten dokument nie jest jeszcze powiązany z zadaniem.")
            return
        if str(record.get("message_identity") or "") == self.tasks_page.uid:
            self.show_page(1)
        else:
            QMessageBox.information(
                self, "Najpierw otwórz wiadomość",
                "Otwórz powiązaną wiadomość i przeanalizuj ją, aby zobaczyć aktualny plan zadań.",
            )
            self._document_message(record)

    @Slot(str)
    def _document_favorite(self, document_id: str) -> None:
        self.document_index.toggle_favorite(document_id)
        self._search_documents(self.documents_page.query.text(), self.documents_page.category())

    @Slot()
    def _repair_document_index(self) -> None:
        worker = FunctionWorker(self.document_index.repair, self.account_address)
        worker.signals.result.connect(self._document_index_repaired)
        worker.signals.failed.connect(lambda message: QMessageBox.warning(self, "Nie udało się sprawdzić indeksu", message))
        self._start_worker(worker)

    @Slot(object)
    def _document_index_repaired(self, result: object) -> None:
        value = result if isinstance(result, dict) else {}
        missing = int(value.get("missing") or 0)
        QMessageBox.information(
            self, "Indeks sprawdzony",
            f"Integralność bazy: {value.get('integrity', 'nieznana')}. "
            f"Brakujące pliki lokalne: {missing}. Żaden wpis ani dokument nie został usunięty.",
        )

    @Slot(object)
    def _document_ocr(self, value: object) -> None:
        record = value if isinstance(value, dict) else {}
        local = Path(str(record.get("local_path") or ""))
        if local.is_file():
            self._index_pdf_background(str(record.get("id") or ""), str(local), ocr=True)
        elif record.get("server_available"):
            self._prepare_server_document(record, "ocr")
        else:
            QMessageBox.warning(self, "OCR niedostępny", "Najpierw wskaż dostępny lokalnie dokument PDF.")

    def _index_pdf_background(self, document_id: str, path: str, ocr: bool = False) -> None:
        if not document_id or not Path(path).is_file():
            return
        self.documents_page.status.setText(
            "Lokalny OCR rozpoznaje tekst…" if ocr else "Indeksowanie tekstu dokumentu w tle…"
        )
        if ocr:
            self.documents_page.set_content_busy(True)
        worker = FunctionWorker(recognize_document_text if ocr else extract_pdf_text, path)
        worker.signals.result.connect(partial(self._pdf_text_ready, document_id, ocr))
        worker.signals.failed.connect(partial(self._pdf_text_failed, document_id, ocr))
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Indeks PDF: {text}"))
        if ocr:
            worker.signals.finished.connect(lambda: self.documents_page.set_content_busy(False))
        self._start_worker(worker)

    @Slot(str, bool, object)
    def _pdf_text_ready(self, document_id: str, ocr: bool, result: object) -> None:
        details = result if isinstance(result, dict) else {}
        text = str(details.get("text") if details else result or "")
        method = str(details.get("method") or ("ocr" if ocr else "pdf_text"))
        used_ocr = ocr and method == "ocr"
        self.document_index.update_text(document_id, text, ocr=used_ocr, status="complete")
        if ocr:
            self.documents_page.status.setText(
                "Treść dokumentu została dodana do wyszukiwania."
                if text.strip() else "Nie znaleziono czytelnego tekstu w dokumencie."
            )
        else:
            self.documents_page.status.setText(
                "Tekst PDF został dodany do wyszukiwarki." if text.strip()
                else "PDF nie zawiera warstwy tekstowej. Możesz opcjonalnie użyć OCR."
            )

    @Slot(str, bool, str)
    def _pdf_text_failed(self, document_id: str, ocr: bool, message: str) -> None:
        if ocr:
            self.document_index.update_text(document_id, "", ocr=True, status="failed")
        self.documents_page.status.setText(message)
        if ocr:
            QMessageBox.information(self, "OCR nie został wykonany", message)

    @Slot(str, bool, int)
    def _save_backup_configuration(self, directory: str, automatic: bool, keep: int) -> bool:
        try:
            chosen = str(Path(directory).expanduser()) if directory.strip() else str(default_backup_directory())
            self.state.backup_directory = chosen
            self.state.automatic_backup = automatic
            self.state.backup_keep = keep
            return True
        except OSError as exc:
            self.settings_page.backup_status.setText(str(exc))
            self._append_diagnostic(f"Ustawienia kopii: {exc}")
            return False

    @Slot()
    def _show_startup_recovery(self) -> None:
        if self._startup_recovery:
            safety = str(self._startup_recovery.get("safety_backup") or "")
            QMessageBox.information(
                self,
                "Odzyskano bezpieczny stan",
                "Poprzednie przywracanie zostało przerwane. Aplikacja automatycznie odtworzyła stan sprzed tej operacji."
                + (f"\n\nUżyta kopia bezpieczeństwa: {safety}" if safety else ""),
            )
        elif self._startup_recovery_error:
            self._append_diagnostic(f"Odzyskiwanie po przerwaniu: {self._startup_recovery_error}")
            QMessageBox.critical(
                self,
                "Wymagane sprawdzenie danych",
                "Wykryto przerwane przywracanie, ale nie udało się automatycznie odzyskać poprzedniego stanu. "
                "Nie wykonuj nowych operacji na dokumentach. Skorzystaj z kopii bezpieczeństwa w Ustawieniach.\n\n"
                + self._startup_recovery_error,
            )

    def _backup_target(self) -> Path:
        directory = Path(self.state.backup_directory or default_backup_directory())
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"asystent-poczty-{datetime.now():%Y%m%d-%H%M%S-%f}.mabackup"

    def _create_backup_and_prune(self, target: str, directory: str, keep: int) -> dict[str, Any]:
        result = self.backup_manager.create_backup(target)
        result["removed"] = self.backup_manager.prune_backups(directory, keep)
        return result

    @Slot()
    def _automatic_backup_if_due(self) -> None:
        if not self.state.automatic_backup or self.state.last_backup_date == datetime.now().date().isoformat():
            return
        if self._backup_running or self._busy_mail or self._analysis_running or self._device_busy or self._reply_running:
            QTimer.singleShot(60000, self._automatic_backup_if_due)
            return
        self._start_backup(False)

    def _start_backup(self, manual: bool = True) -> None:
        if self._backup_running:
            if manual:
                self.settings_page.backup_status.setText("Kopia zapasowa jest już tworzona.")
            return
        self._backup_manual = manual
        try:
            saved = self._save_backup_configuration(
                self.settings_page.backup_directory.text(),
                self.settings_page.automatic_backup.isChecked(),
                self.settings_page.backup_keep.value(),
            )
            if not saved:
                if manual:
                    QMessageBox.warning(
                        self, "Nie udało się zapisać ustawień kopii", self.settings_page.backup_status.text()
                    )
                self._backup_manual = False
                return
            target = self._backup_target()
        except (OSError, ValueError) as exc:
            self._backup_failed(str(exc))
            self._backup_manual = False
            return
        self._backup_running = True
        self.settings_page.set_backup_busy(True, "Tworzenie szyfrowanej kopii zapasowej…")
        self.statusBar().showMessage("Tworzenie kopii zapasowej…")
        worker = FunctionWorker(
            self._create_backup_and_prune,
            str(target), str(target.parent), self.state.backup_keep,
        )
        worker.signals.result.connect(self._backup_ready)
        worker.signals.failed.connect(self._backup_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Kopia zapasowa: {text}"))
        worker.signals.finished.connect(self._backup_finished)
        self._start_worker(worker)

    @Slot(object)
    def _backup_ready(self, result: object) -> None:
        details = result if isinstance(result, dict) else {}
        path = str(details.get("path") or "")
        try:
            self.state.last_backup_date = datetime.now().date().isoformat()
        except OSError as exc:
            self._append_diagnostic(f"Data kopii: {exc}")
        text = f"Kopia została bezpiecznie zapisana: {path}" if path else "Kopia została bezpiecznie zapisana."
        self.settings_page.backup_status.setText(text)
        self.statusBar().showMessage("Kopia zapasowa została utworzona", 5000)
        if self._backup_manual:
            QMessageBox.information(self, "Kopia zapasowa gotowa", text)

    @Slot(str)
    def _backup_failed(self, message: str) -> None:
        self.settings_page.backup_status.setText(f"Nie udało się utworzyć kopii: {message}")
        self.statusBar().showMessage("Kopia zapasowa nie została utworzona", 5000)
        if self._backup_manual:
            QMessageBox.warning(self, "Nie udało się utworzyć kopii", message)

    @Slot()
    def _backup_finished(self) -> None:
        self._backup_running = False
        self.settings_page.set_backup_busy(False)
        self._backup_manual = False
        if self._shutdown_pending:
            self._start_shutdown()

    @Slot()
    def _choose_restore_backup(self) -> None:
        if self._backup_running or self._busy_mail or self._analysis_running or self._device_busy or self._reply_running:
            QMessageBox.information(self, "Operacja jeszcze trwa", "Poczekaj na zakończenie bieżącej operacji.")
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Wybierz kopię zapasową", self.state.backup_directory,
            "Kopia Asystenta Poczty (*.mabackup)",
        )
        if not source:
            return
        self._backup_running = True
        self._backup_manual = True
        self.settings_page.set_backup_busy(True, "Sprawdzanie integralności wybranej kopii…")
        worker = FunctionWorker(self.backup_manager.inspect_backup, source)
        worker.signals.result.connect(partial(self._restore_inspected, source))
        worker.signals.failed.connect(self._restore_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Sprawdzanie kopii: {text}"))
        worker.signals.finished.connect(self._restore_inspection_finished)
        self._start_worker(worker)

    @Slot(str, object)
    def _restore_inspected(self, source: str, result: object) -> None:
        details = result if isinstance(result, dict) else {}
        answer = QMessageBox.question(
            self,
            "Przywrócić dane?",
            "Kopia jest poprawna.\n\n"
            f"Utworzona: {details.get('created_at', 'brak daty')}\n"
            f"Dokumenty: {details.get('documents', 0)}\n"
            f"Wersje robocze: {details.get('drafts', 0)}\n\n"
            "Obecny stan zostanie najpierw zapisany w dodatkowej kopii bezpieczeństwa. "
            "Następnie lokalne ustawienia, zadania, dokumenty i wersje robocze zostaną zastąpione.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.settings_page.backup_status.setText("Przywracanie anulowano. Nie zmieniono danych.")
            return
        self._start_restore(source)

    @Slot()
    def _restore_inspection_finished(self) -> None:
        # Po potwierdzeniu od razu rusza drugi pracownik; wtedy flaga pozostaje aktywna.
        if not getattr(self, "_restore_started", False):
            self._backup_running = False
            self._backup_manual = False
            self.settings_page.set_backup_busy(False)

    def _start_restore(self, source: str) -> None:
        self._restore_started = True
        self.settings_page.set_backup_busy(True, "Przywracanie danych i kontrola spójności…")
        worker = FunctionWorker(self.backup_manager.restore_backup, source, self.state.backup_directory)
        worker.signals.result.connect(self._restore_ready)
        worker.signals.failed.connect(self._restore_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Przywracanie: {text}"))
        worker.signals.finished.connect(self._restore_finished)
        self._start_worker(worker)

    @Slot(object)
    def _restore_ready(self, result: object) -> None:
        details = result if isinstance(result, dict) else {}
        safety = str(details.get("safety_backup") or "")
        self.settings_page.backup_status.setText("Dane zostały przywrócone. Zamknij i uruchom aplikację ponownie.")
        QMessageBox.information(
            self,
            "Dane przywrócone",
            "Przywracanie zakończyło się pomyślnie. Uruchom aplikację ponownie, aby wczytać cały stan."
            + (f"\n\nKopia poprzedniego stanu: {safety}" if safety else ""),
        )

    @Slot(str)
    def _restore_failed(self, message: str) -> None:
        self.settings_page.backup_status.setText(f"Nie udało się przywrócić danych: {message}")
        QMessageBox.warning(self, "Nie udało się przywrócić danych", message)

    @Slot()
    def _restore_finished(self) -> None:
        self._restore_started = False
        self._backup_running = False
        self._backup_manual = False
        self.settings_page.set_backup_busy(False)
        if self._shutdown_pending:
            self._start_shutdown()

    @Slot()
    def _check_ollama(self) -> None:
        self.settings_page.ollama_button.setEnabled(False)
        self.settings_page.ollama_button.setText("Sprawdzanie Ollamy…")
        self.settings_page.ollama_status.setText("Sprawdzanie lokalnej Ollamy…")
        worker = FunctionWorker(check_ollama)
        worker.signals.result.connect(self._ollama_ready)
        worker.signals.failed.connect(self._ollama_failed)
        worker.signals.diagnostic.connect(lambda text: self._append_diagnostic(f"Test Ollamy: {text}"))
        worker.signals.finished.connect(self._ollama_check_finished)
        self._start_worker(worker)

    @Slot()
    def _ollama_check_finished(self) -> None:
        self.settings_page.ollama_button.setEnabled(True)
        self.settings_page.ollama_button.setText("Sprawdź Ollamę")

    @Slot(object)
    def _ollama_ready(self, result: object) -> None:
        version = result.get("version", "uruchomiona") if isinstance(result, dict) else "uruchomiona"
        embedding_ready = bool(result.get("embedding_ready")) if isinstance(result, dict) else False
        if embedding_ready:
            search_text = f"Model wyszukiwania: {EMBEDDING_MODEL}."
        else:
            search_text = (
                f"Do wyszukiwania po opisie brakuje modelu {EMBEDDING_MODEL}. "
                f"Zainstaluj go poleceniem: ollama pull {EMBEDDING_MODEL}"
            )
        self.settings_page.ollama_status.setText(
            f"Ollama działa (wersja {version}). Model analizy: {MODEL_NAME}. {search_text}"
        )

    @Slot(str)
    def _ollama_failed(self, message: str) -> None:
        self.settings_page.ollama_status.setText(message)
        self._append_diagnostic(f"Test Ollamy: {message}")

    @Slot(int, bool)
    def _set_appearance(self, scale: int, high_contrast: bool) -> None:
        self.state.font_scale = scale
        self.state.high_contrast = high_contrast
        self._apply_appearance(scale, high_contrast)

    def _apply_appearance(self, scale: int, high_contrast: bool) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(scale, high_contrast))
        if hasattr(self, "sidebar"):
            self._update_responsive_layout(self.width())

    def _append_diagnostic(self, text: str) -> None:
        self.settings_page.diagnostics.append(text)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if self._closing:
            event.ignore()
            return
        if (
            self._busy_mail or self._analysis_running or self._device_busy or self._reply_running
            or self._backup_running or self._semantic_current_uid or self._semantic_search_running
        ):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Operacja jeszcze trwa")
            box.setText("Trwa pobieranie, analiza, kopia zapasowa albo operacja urządzenia. Aplikacja może bezpiecznie zamknąć się po jej zakończeniu.")
            wait_button = box.addButton("Zamknij po zakończeniu", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Wróć do aplikacji", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is wait_button:
                self._shutdown_pending = True
                self.setEnabled(False)
                self.statusBar().showMessage("Aplikacja zamknie się po zakończeniu operacji…")
            event.ignore()
            return
        event.ignore()
        self._start_shutdown()

    @Slot()
    def _start_shutdown(self) -> None:
        if (
            self._closing or self._busy_mail or self._analysis_running or self._device_busy
            or self._reply_running or self._backup_running or self._semantic_current_uid
            or self._semantic_search_running
        ):
            return
        self._closing = True
        self._cancel_semantic_index()
        self._session_password = ""
        self._pending_password = ""
        self._shutdown_pending = True
        self.setEnabled(False)
        self.statusBar().showMessage("Bezpieczne zamykanie połączenia z pocztą…")
        if self.mail_thread.isRunning():
            self.disconnect_mail.emit()
            self._shutdown_timer.start(3500)
        else:
            self._shutdown_finished()

    @Slot()
    def _mail_disconnected(self) -> None:
        self.mail_thread.quit()

    @Slot()
    def _shutdown_timeout(self) -> None:
        if not self.mail_thread.isRunning():
            return
        # IMAP logout może czekać na niedostępny serwer. Zamknięcie gniazda
        # przerywa oczekiwanie bez blokowania głównego wątku interfejsu.
        self.mail_service.abort()
        self.mail_thread.quit()
        QTimer.singleShot(1500, self._verify_shutdown)

    @Slot()
    def _verify_shutdown(self) -> None:
        if self.mail_thread.isRunning():
            self._closing = False
            self._shutdown_pending = False
            self.setEnabled(True)
            self.statusBar().showMessage("Nie udało się bezpiecznie zamknąć połączenia.")
            QMessageBox.warning(
                self,
                "Nie można jeszcze zamknąć",
                "Połączenie z pocztą nadal kończy pracę. Spróbuj zamknąć aplikację ponownie za chwilę.",
            )

    @Slot()
    def _shutdown_finished(self) -> None:
        if not self._shutdown_pending and not self._closing:
            return
        self._shutdown_timer.stop()
        self._allow_close = True
        QTimer.singleShot(0, self.close)

