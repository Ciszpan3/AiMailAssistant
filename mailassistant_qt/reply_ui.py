"""Widoki przygotowania i końcowej kontroli odpowiedzi e-mail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    return frame, layout


class ReplyPage(QWidget):
    """Edytor odpowiedzi. Sam widok nigdy nie wykonuje wysyłki."""

    draft_changed = Signal(dict)
    ai_requested = Signal()
    review_requested = Signal()
    save_requested = Signal(dict)
    sent_check_requested = Signal()
    delete_requested = Signal()
    help_requested = Signal()
    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.context: dict[str, Any] = {}
        self._loading = False
        self._attachments: list[dict[str, Any]] = []
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.setInterval(900)
        self.autosave.timeout.connect(self._emit_changed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(26, 24, 26, 30)
        root.setSpacing(16)

        page_header = QHBoxLayout()
        self.page_title = QLabel("Odpowiedź")
        self.page_title.setObjectName("PageTitle")
        page_header.addWidget(self.page_title, 1)
        help_button = QPushButton("Potrzebuję pomocy")
        help_button.setObjectName("QuietButton")
        help_button.clicked.connect(self.help_requested)
        page_header.addWidget(help_button)
        root.addLayout(page_header)
        self.back_button = QPushButton("← Wróć")
        self.back_button.setObjectName("SecondaryButton")
        self.back_button.clicked.connect(self.back_requested)
        root.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.status = QLabel("Wybierz wiadomość i kliknij „Przygotuj odpowiedź”.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        header, header_layout = _card()
        section = QLabel("Adres i temat")
        section.setObjectName("SectionTitle")
        header_layout.addWidget(section)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.recipient = QLineEdit()
        self.recipient.setAccessibleName("Adres odbiorcy")
        self.recipient.setPlaceholderText("adres@przyklad.pl")
        self.subject = QLineEdit()
        self.subject.setAccessibleName("Temat wiadomości")
        form.addRow("Do:", self.recipient)
        form.addRow("Temat:", self.subject)
        header_layout.addLayout(form)
        self.recipient_warning = QLabel("")
        self.recipient_warning.setWordWrap(True)
        self.recipient_warning.setStyleSheet("color: #9a3412;")
        self.recipient_warning.hide()
        header_layout.addWidget(self.recipient_warning)
        root.addWidget(header)

        editor, editor_layout = _card()
        section = QLabel("Treść odpowiedzi")
        section.setObjectName("SectionTitle")
        editor_layout.addWidget(section)
        self.body = QTextEdit()
        self.body.setAccessibleName("Treść wiadomości")
        self.body.setAcceptRichText(False)
        self.body.setMinimumHeight(230)
        self.body.setPlaceholderText("Napisz odpowiedź własnymi słowami albo poproś lokalną Ollamę o propozycję.")
        editor_layout.addWidget(self.body)
        ai_row = QHBoxLayout()
        self.ai_button = QPushButton("Pomóż mi napisać odpowiedź")
        self.ai_button.setObjectName("SecondaryButton")
        self.ai_button.clicked.connect(self.ai_requested)
        ai_row.addWidget(self.ai_button)
        ai_row.addStretch(1)
        editor_layout.addLayout(ai_row)
        hint = QLabel("Propozycja powstaje lokalnie. Zawsze przeczytaj ją przed wysłaniem.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        editor_layout.addWidget(hint)
        root.addWidget(editor)

        files, files_layout = _card()
        section = QLabel("Załączniki PDF")
        section.setObjectName("SectionTitle")
        files_layout.addWidget(section)
        self.attachments = QListWidget()
        self.attachments.setAccessibleName("Załączniki wiadomości")
        self.attachments.setMinimumHeight(105)
        files_layout.addWidget(self.attachments)
        file_buttons = QGridLayout()
        self.add_button = QPushButton("Dodaj PDF")
        self.add_button.setObjectName("SecondaryButton")
        self.add_button.clicked.connect(self._add_pdf)
        self.remove_button = QPushButton("Usuń z odpowiedzi")
        self.remove_button.setObjectName("SecondaryButton")
        self.remove_button.clicked.connect(self._remove_pdf)
        self.preview_button = QPushButton("Pokaż PDF")
        self.preview_button.setObjectName("SecondaryButton")
        self.preview_button.clicked.connect(self._preview_pdf)
        file_buttons.addWidget(self.add_button, 0, 0)
        file_buttons.addWidget(self.preview_button, 0, 1)
        file_buttons.addWidget(self.remove_button, 1, 0, 1, 2)
        file_buttons.setColumnStretch(0, 1)
        file_buttons.setColumnStretch(1, 1)
        files_layout.addLayout(file_buttons)
        root.addWidget(files)

        actions, actions_layout = _card()
        self.review_button = QPushButton("Sprawdź przed wysłaniem")
        self.review_button.clicked.connect(self.review_requested)
        self.save_button = QPushButton("Zapisz wersję roboczą")
        self.save_button.setObjectName("SecondaryButton")
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.draft()))
        self.check_sent_button = QPushButton("Sprawdź folder Wysłane")
        self.check_sent_button.setObjectName("SecondaryButton")
        self.check_sent_button.clicked.connect(self.sent_check_requested)
        self.delete_button = QPushButton("Usuń wersję roboczą")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.clicked.connect(self.delete_requested)
        action_row = QVBoxLayout()
        action_row.addWidget(self.review_button)
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.check_sent_button)
        action_row.addWidget(self.delete_button)
        actions_layout.addLayout(action_row)
        root.addWidget(actions)
        root.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        self.recipient.textChanged.connect(self._edited)
        self.subject.textChanged.connect(self._edited)
        self.body.textChanged.connect(self._edited)
        QWidget.setTabOrder(self.recipient, self.subject)
        QWidget.setTabOrder(self.subject, self.body)
        QWidget.setTabOrder(self.body, self.ai_button)
        QWidget.setTabOrder(self.ai_button, self.attachments)
        QWidget.setTabOrder(self.attachments, self.add_button)
        QWidget.setTabOrder(self.add_button, self.preview_button)
        QWidget.setTabOrder(self.preview_button, self.remove_button)
        QWidget.setTabOrder(self.remove_button, self.review_button)
        self.set_enabled(False)

    def set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.recipient, self.subject, self.body, self.ai_button, self.add_button,
            self.remove_button, self.preview_button, self.review_button, self.delete_button,
            self.save_button,
        ):
            widget.setEnabled(enabled)

    def set_context(self, context: dict[str, Any], draft: dict[str, Any] | None = None) -> None:
        self._loading = True
        self.context = dict(context)
        value = dict(context)
        if draft:
            value.update(draft)
        self.page_title.setText("Nowa wiadomość" if value.get("is_new") else "Odpowiedź")
        self.recipient.setText(str(value.get("to") or ""))
        self.subject.setText(str(value.get("subject") or ""))
        self.body.setPlainText(str(value.get("body") or ""))
        self._attachments = [dict(item) for item in value.get("attachments", []) if isinstance(item, dict)]
        self._refresh_attachments()
        self.status.setText("Wersja robocza jest zapisywana automatycznie na tym komputerze.")
        self.set_enabled(True)
        self.check_sent_button.setEnabled(bool(value.get("message_id")))
        self._loading = False
        self._update_recipient_warning()

    def reset(self) -> None:
        """Czyści szkic z ekranu bez usuwania jego zapisanej kopii."""
        self.autosave.stop()
        self._loading = True
        self.context = {}
        self.page_title.setText("Odpowiedź")
        self.recipient.clear()
        self.subject.clear()
        self.body.clear()
        self._attachments = []
        self._refresh_attachments()
        self.recipient_warning.clear()
        self.recipient_warning.hide()
        self.status.setText("Zaloguj się, a następnie wybierz wiadomość, aby przygotować odpowiedź.")
        self.set_enabled(False)
        self.check_sent_button.setEnabled(False)
        self._loading = False

    def draft(self) -> dict[str, Any]:
        value = dict(self.context)
        value.update({
            "to": self.recipient.text().strip(),
            "subject": self.subject.text().strip(),
            "body": self.body.toPlainText(),
            "attachments": [dict(item) for item in self._attachments],
        })
        return value

    def add_attachment(self, path: str, approved: bool = False) -> None:
        resolved = str(Path(path).resolve())
        if any(str(item.get("path", "")).lower() == resolved.lower() for item in self._attachments):
            return
        candidate = Path(resolved)
        self._attachments.append({
            "path": resolved,
            "name": candidate.name,
            "size": candidate.stat().st_size if candidate.is_file() else 0,
            "approved": approved,
        })
        self._refresh_attachments()
        self._edited()

    def set_generated_body(self, text: str) -> None:
        self.body.setPlainText(text)
        self.status.setText("Ollama przygotowała propozycję. Przeczytaj ją i popraw przed wysłaniem.")

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.ai_button.setEnabled(not busy)
        self.review_button.setEnabled(not busy)
        lowered = text.lower()
        self.ai_button.setText(
            "Wysyłanie wiadomości…" if busy and "wysył" in lowered
            else "Generowanie odpowiedzi…" if busy
            else "Pomóż mi napisać odpowiedź"
        )
        self.review_button.setText("Operacja w toku…" if busy else "Sprawdź przed wysłaniem")
        if text:
            self.status.setText(text)

    def _refresh_attachments(self) -> None:
        self.attachments.clear()
        for item in self._attachments:
            name = str(item.get("name") or Path(str(item.get("path") or "")).name)
            size = int(item.get("size") or 0)
            checked = "sprawdzony" if item.get("approved") else "do sprawdzenia"
            row = QListWidgetItem(f"{name} — {size / 1024:.0f} KB — {checked}")
            row.setData(Qt.ItemDataRole.UserRole, str(item.get("path") or ""))
            self.attachments.addItem(row)
        if self._attachments:
            self.attachments.setCurrentRow(0)

    @Slot()
    def _add_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Dodaj dokument do odpowiedzi", "", "Dokument PDF (*.pdf)")
        if path:
            self.add_attachment(path, approved=False)

    @Slot()
    def _remove_pdf(self) -> None:
        row = self.attachments.currentRow()
        if 0 <= row < len(self._attachments):
            self._attachments.pop(row)
            self._refresh_attachments()
            self._edited()

    @Slot()
    def _preview_pdf(self) -> None:
        row = self.attachments.currentRow()
        if not (0 <= row < len(self._attachments)):
            QMessageBox.information(self, "Wybierz dokument", "Najpierw wybierz PDF z listy.")
            return
        path = str(self._attachments[row].get("path") or "")
        if not Path(path).is_file():
            QMessageBox.warning(self, "Brak pliku", "Nie można odnaleźć wybranego dokumentu.")
            return
        PdfPreviewDialog(path, self).exec()

    @Slot()
    def _edited(self) -> None:
        if self._loading:
            return
        self._update_recipient_warning()
        self.autosave.start()

    def _update_recipient_warning(self) -> None:
        original = str(self.context.get("original_recipient") or "").strip().lower()
        current = self.recipient.text().strip().lower()
        changed = bool(original and current and original not in current)
        self.recipient_warning.setText(
            "Uwaga: adres odbiorcy został zmieniony względem oryginalnej wiadomości."
            if changed else ""
        )
        self.recipient_warning.setVisible(changed)

    @Slot()
    def _emit_changed(self) -> None:
        if self.context:
            self.draft_changed.emit(self.draft())


class ReviewDialog(QDialog):
    """Ostatnia, jawna kontrola. Accept oznacza wyłącznie zgodę na wysyłkę."""

    def __init__(self, draft: dict[str, Any], warnings: list[dict[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sprawdź wiadomość przed wysłaniem")
        self.setMinimumSize(500, 360)
        self.resize(780, 720)
        root = QVBoxLayout(self)
        title = QLabel("Sprawdź wiadomość przed wysłaniem")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        notice = QLabel("Sprawdź adres odbiorcy, treść i załączniki. Nic nie zostanie wysłane bez Twojej decyzji.")
        notice.setWordWrap(True)
        root.addWidget(notice)

        summary = QTextEdit()
        summary.setReadOnly(True)
        files = list(draft.get("attachments") or [])
        file_lines = [
            f"• {item.get('name') or Path(str(item.get('path') or '')).name} "
            f"({int(item.get('size') or 0) / 1024:.0f} KB)"
            for item in files if isinstance(item, dict)
        ]
        summary.setPlainText(
            f"DO:\n{draft.get('to', '')}\n\nTEMAT:\n{draft.get('subject', '')}\n\n"
            f"TREŚĆ:\n{draft.get('body', '')}\n\nZAŁĄCZNIKI:\n"
            + ("\n".join(file_lines) if file_lines else "Brak")
        )
        root.addWidget(summary, 1)
        self.attachment_list = QListWidget()
        for item in files:
            if not isinstance(item, dict):
                continue
            row = QListWidgetItem(str(item.get("name") or Path(str(item.get("path") or "")).name))
            row.setData(Qt.ItemDataRole.UserRole, str(item.get("path") or ""))
            self.attachment_list.addItem(row)
        if self.attachment_list.count():
            self.attachment_list.setMaximumHeight(90)
            self.attachment_list.setCurrentRow(0)
            root.addWidget(self.attachment_list)
            preview_button = QPushButton("Pokaż wybrany PDF")
            preview_button.setObjectName("SecondaryButton")
            preview_button.clicked.connect(self._preview_attachment)
            root.addWidget(preview_button)

        blockers = [item["text"] for item in warnings if item.get("level") == "blocker"]
        cautions = [item["text"] for item in warnings if item.get("level") != "blocker"]
        warning_label = QLabel()
        warning_label.setWordWrap(True)
        warning_label.setText(
            ("Błędy blokujące:\n" + "\n".join(f"• {item}" for item in blockers) if blockers else "")
            + ("\n\nDo sprawdzenia:\n" + "\n".join(f"• {item}" for item in cautions) if cautions else "")
            or "Nie wykryto ostrzeżeń. Mimo to przeczytaj całą odpowiedź."
        )
        root.addWidget(warning_label)

        buttons = QDialogButtonBox()
        back = buttons.addButton("Wróć do edycji", QDialogButtonBox.ButtonRole.RejectRole)
        send = buttons.addButton("Wyślij wiadomość", QDialogButtonBox.ButtonRole.AcceptRole)
        send.setEnabled(not blockers)
        back.clicked.connect(self.reject)
        send.clicked.connect(self.accept)
        root.addWidget(buttons)

    @Slot()
    def _preview_attachment(self) -> None:
        item = self.attachment_list.currentItem()
        path = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
        if not Path(path).is_file():
            QMessageBox.warning(self, "Brak pliku", "Nie można odnaleźć wybranego dokumentu.")
            return
        PdfPreviewDialog(path, self).exec()


class PdfPreviewDialog(QDialog):
    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Podgląd: {Path(path).name}")
        self.setMinimumSize(500, 360)
        self.resize(850, 700)
        layout = QVBoxLayout(self)
        self.document = QPdfDocument(self)
        self.document.load(path)
        self.preview = QPdfView()
        self.preview.setDocument(self.document)
        self.preview.setPageMode(QPdfView.PageMode.MultiPage)
        self.preview.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        layout.addWidget(self.preview, 1)
        close = QPushButton("Zamknij podgląd")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def done(self, result: int) -> None:
        self.preview.setDocument(None)
        self.document.close()
        super().done(result)


def confirm_delete_draft(parent: QWidget) -> bool:
    return QMessageBox.question(
        parent,
        "Usunąć wersję roboczą?",
        "Treść i lista załączników tej odpowiedzi zostaną usunięte z tego komputera.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes
