"""Wspólny, dostępny system wizualny aplikacji."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


def ensure_readable_font() -> None:
    """Ładuje Segoe UI, gdy izolowane środowisko Qt nie widzi czcionek systemu."""
    families = set(QFontDatabase.families())
    if "Segoe UI" not in families and os.name == "nt":
        font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
            path = font_dir / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    app = QApplication.instance()
    if app is not None and "Segoe UI" in set(QFontDatabase.families()):
        app.setFont(QFont("Segoe UI", 11))


def stylesheet(scale: int = 100, high_contrast: bool = False) -> str:
    base = max(13, round(15 * scale / 100))
    small = max(12, round(13 * scale / 100))
    title = max(24, round(29 * scale / 100))
    hero = max(28, round(34 * scale / 100))
    control = max(28, round(30 * scale / 100))
    radius = max(10, round(12 * scale / 100))
    if high_contrast:
        background, card, soft, text, muted, border = "#ffffff", "#ffffff", "#f2f2f2", "#000000", "#202020", "#000000"
        primary, primary_hover, primary_pressed, navy = "#0047b3", "#003a94", "#002b70", "#001b44"
        selected, focus = "#d8e8ff", "#ffbf00"
    else:
        background, card, soft, text, muted, border = "#f5f7fb", "#ffffff", "#eef4fb", "#17243a", "#58677a", "#d7e0eb"
        primary, primary_hover, primary_pressed, navy = "#1769e0", "#0f58c4", "#0b469d", "#17345c"
        selected, focus = "#e4efff", "#f4a300"
    return f"""
        QWidget {{
            background: {background}; color: {text};
            font-family: 'Segoe UI'; font-size: {base}px;
        }}
        QMainWindow, QDialog {{ background: {background}; }}
        QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
        QLabel#AppTitle {{ color: white; font-size: {title}px; font-weight: 700; background: transparent; }}
        QLabel#PageTitle {{ color: {navy}; font-size: {title}px; font-weight: 700; }}
        QLabel#HeroTitle {{ color: {navy}; font-size: {hero}px; font-weight: 700; }}
        QLabel#SectionTitle {{ color: {navy}; font-size: {base + 3}px; font-weight: 700; }}
        QLabel#Eyebrow {{ color: {primary}; font-size: {small}px; font-weight: 700; }}
        QLabel#Muted {{ color: {muted}; font-size: {small}px; }}
        QLabel#Deadline {{ color: #9a3412; font-weight: 700; }}
        QLabel#Badge {{ color: {navy}; background: {soft}; border: 1px solid {border}; border-radius: 8px; padding: 3px 8px; font-size: {small}px; font-weight: 650; }}
        QLabel#MailStatus {{ color: {primary}; background: transparent; border: 0; padding: 0; font-size: {small}px; font-weight: 700; }}
        QWidget#MailStatusPanel {{ background: transparent; }}
        QLabel#StatusSuccess {{ color: #11643a; background: #e8f7ee; border-radius: 9px; padding: 10px 12px; }}
        QLabel#StatusWarning {{ color: #8a3c08; background: #fff3df; border-radius: 9px; padding: 10px 12px; }}
        QLabel#StatusError {{ color: #9f1c1c; background: #fdecec; border-radius: 9px; padding: 10px 12px; }}
        QLabel#StatusInfo {{ color: {navy}; background: {soft}; border-radius: 9px; padding: 10px 12px; }}
        QFrame#Sidebar {{ background: {navy}; border: none; }}
        QFrame#Card {{ background: {card}; border: 1px solid {border}; border-radius: {radius + 3}px; }}
        QFrame#SubtleCard {{ background: {soft}; border: 1px solid {border}; border-radius: {radius}px; }}
        QFrame#MailRow {{ background: {card}; border: 0; border-radius: {radius}px; }}
        QFrame#MailRow:hover {{ background: {soft}; }}
        QPushButton {{
            background: {primary}; color: white; border: 2px solid {primary}; border-radius: {radius}px;
            padding: 10px 16px; min-height: {control}px; font-weight: 650;
        }}
        QPushButton:hover {{ background: {primary_hover}; border-color: {primary_hover}; }}
        QPushButton:pressed {{ background: {primary_pressed}; border-color: {primary_pressed}; }}
        QPushButton:focus {{ border: 3px solid {focus}; padding: 9px 15px; }}
        QPushButton:disabled {{ background: #a9b4c2; border-color: #a9b4c2; color: #f5f7fa; }}
        QPushButton#NavButton {{ text-align: left; background: transparent; border: 0; border-left: 4px solid transparent; border-radius: 8px; padding: 12px 13px; min-height: {control}px; }}
        QPushButton#NavButton:hover {{ background: rgba(255,255,255,0.13); }}
        QPushButton#NavButton:checked {{ background: rgba(255,255,255,0.20); border-left-color: white; }}
        QPushButton#NavButton:focus {{ border: 3px solid {focus}; border-left: 4px solid white; }}
        QPushButton#NavButton[collapsed="true"] {{ text-align: center; padding: 10px 8px; }}
        QPushButton#SecondaryButton {{ background: {card}; color: {primary}; border: 2px solid {primary}; }}
        QPushButton#SecondaryButton:hover {{ background: {selected}; }}
        QPushButton#QuietButton {{ background: transparent; color: {primary}; border: 2px solid transparent; }}
        QPushButton#QuietButton:hover {{ background: {selected}; }}
        QPushButton#SeniorAction {{
            text-align: left; font-size: {base + 3}px; padding: 18px 22px;
            min-height: 78px; border-radius: {radius + 3}px;
        }}
        QPushButton#SeniorContinue {{
            font-size: {base + 3}px; padding: 16px 22px; min-height: {control + 12}px;
            background: #137a46; border-color: #137a46; border-radius: {radius + 2}px;
        }}
        QPushButton#DangerButton {{ background: #b42318; border-color: #b42318; }}
        QLineEdit, QComboBox, QSpinBox {{
            background: {card}; border: 1px solid {border}; border-radius: {radius}px;
            padding: 9px 11px; selection-background-color: {primary}; min-height: {control}px;
        }}
        QTextBrowser, QTextEdit, QListWidget {{
            background: {card}; border: 1px solid {border}; border-radius: {radius}px;
            padding: 9px 11px; selection-background-color: {primary};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextBrowser:focus, QTextEdit:focus, QListWidget:focus {{ border: 3px solid {focus}; padding: 7px 9px; }}
        QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled {{ background: {soft}; color: {muted}; }}
        QComboBox::drop-down {{ border: 0; width: 34px; }}
        QListWidget {{ outline: none; }}
        QListWidget::item {{ padding: 10px; border-bottom: 1px solid {border}; border-radius: 8px; }}
        QListWidget::item:hover {{ background: {soft}; }}
        QListWidget::item:selected {{ background: {selected}; color: {navy}; border: 2px solid {primary}; }}
        QListWidget#MessageList::item {{ margin: 4px 2px; padding: 4px; }}
        QTreeWidget#MessageList {{ background: {card}; border: 1px solid {border}; border-radius: {radius}px; outline: none; }}
        QTreeWidget#MessageList::item {{ padding: 8px 6px; border-bottom: 1px solid {border}; }}
        QTreeWidget#MessageList::item:hover {{ background: {soft}; }}
        QTreeWidget#MessageList::item:selected {{ background: {selected}; color: {navy}; }}
        QListWidget#SemanticResults::item {{ border: 0; background: transparent; padding: 2px; }}
        QCheckBox {{ spacing: 10px; min-height: {control}px; }}
        QCheckBox::indicator {{ width: 25px; height: 25px; }}
        QProgressBar {{ border: 1px solid {border}; border-radius: 8px; text-align: center; background: {card}; min-height: 20px; }}
        QProgressBar::chunk {{ background: {primary}; border-radius: 7px; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 14px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: #9eacbd; min-height: 36px; border-radius: 6px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QSplitter::handle {{ background: {border}; width: 3px; height: 3px; }}
        QToolButton {{ background: transparent; border: 2px solid transparent; color: {primary}; font-weight: 650; padding: 7px; }}
        QToolButton:hover {{ background: {selected}; border-radius: 8px; }}
        QToolButton:focus {{ border: 3px solid {focus}; border-radius: 8px; }}
        QToolTip {{ background: {navy}; color: white; border: 0; padding: 7px; }}
        QStatusBar {{ background: {card}; border-top: 1px solid {border}; color: {muted}; }}
    """

