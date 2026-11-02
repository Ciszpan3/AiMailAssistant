"""Wykrywanie urządzeń, bezpieczny wydruk PDF i skanowanie WIA.

Moduł nie zna interfejsu aplikacji. Wszystkie operacje są uruchamiane wyłącznie
po działaniu użytkownika i mogą być wykonane przez ``FunctionWorker``.
"""

from __future__ import annotations

import json
import gc
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QRect, QSize, QStandardPaths, Qt
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

from .core import UserFacingError
from .storage import ensure_free_space, temporary_sibling


MAX_PDF_BYTES = 100 * 1024 * 1024
JPEG_FORMAT = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"


def runtime_cache_dir() -> Path:
    preferred = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(preferred, 0o700)
        except OSError:
            pass
        return preferred
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "MailAssistant" / "cache"
        fallback.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(fallback, 0o700)
        except OSError:
            pass
        return fallback


@dataclass(slots=True)
class Device:
    id: str
    name: str
    kind: str
    manufacturer: str = ""
    model: str = ""
    connection: str = "nieznane"
    status: str = "nieznany"
    status_detail: str = ""
    is_default: bool = False
    is_virtual: bool = False
    capabilities: dict[str, Any] = field(default_factory=dict)
    hardware_id: str = ""
    driver: str = ""
    group_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _manufacturer_model(name: str, make_model: str = "") -> tuple[str, str]:
    text = (make_model or name).strip()
    known = ("Brother", "Canon", "Epson", "HP", "Lexmark", "Xerox", "Samsung", "Kyocera")
    for vendor in known:
        if re.search(rf"\b{re.escape(vendor)}\b", text, re.I):
            model = re.sub(rf"\b{re.escape(vendor)}\b", "", text, flags=re.I).strip(" -")
            return vendor, model or text
    return "", text


def _connection(port: str, remote: bool) -> str:
    value = port.upper()
    if remote or value.startswith(("IP_", "WSD", "HTTP", "TCP")) or "\\\\" in port:
        return "sieć"
    if value.startswith("USB"):
        return "USB"
    if value in {"PORTPROMPT:", "NUL:", "SHRFAX:"}:
        return "wirtualne"
    return "lokalne" if port else "nieznane"


def _printer_status(row: dict[str, Any], qt_state: str) -> tuple[str, str]:
    if bool(row.get("WorkOffline")):
        return "offline", "Windows zgłasza, że urządzenie jest offline."
    code = int(row.get("PrinterStatus") or 0)
    mapping = {3: "gotowa", 4: "drukuje", 5: "uruchamia się", 6: "zatrzymana", 7: "offline"}
    if code in mapping:
        return mapping[code], "Stan podany przez usługę drukowania Windows."
    qt_mapping = {
        "Idle": "gotowa", "Active": "drukuje", "Aborted": "błąd", "Error": "błąd",
    }
    return qt_mapping.get(qt_state, "nieznany"), "Sterownik nie podał dokładnego stanu."


def _windows_printer_rows() -> dict[str, dict[str, Any]]:
    """Pobiera uzupełniające dane Windows. Błąd nie blokuje listy Qt."""
    if os.name != "nt":
        return {}
    script = (
        "Get-CimInstance Win32_Printer | Select-Object Name,DriverName,PortName,"
        "PrinterStatus,WorkOffline,Default,Network,Local,PNPDeviceID | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or not result.stdout.strip():
            return {}
        payload = json.loads(result.stdout)
        rows = payload if isinstance(payload, list) else [payload]
        return {_safe_text(row.get("Name")): row for row in rows if isinstance(row, dict)}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def detect_printers() -> list[Device]:
    windows = _windows_printer_rows()
    devices: list[Device] = []
    virtual_markers = ("pdf", "onenote", "fax", "xps", "document writer")
    for name in QPrinterInfo.availablePrinterNames():
        info = QPrinterInfo.printerInfo(name)
        row = windows.get(name, {})
        maker, model = _manufacturer_model(name, info.makeAndModel())
        port = _safe_text(row.get("PortName"))
        status, detail = _printer_status(row, getattr(info.state(), "name", str(info.state())))
        pages = []
        try:
            pages = [page.name() for page in info.supportedPageSizes() if page.isValid()]
        except Exception:
            pages = []
        devices.append(Device(
            id=f"printer:{name}", name=name, kind="printer", manufacturer=maker, model=model,
            connection=_connection(port, info.isRemote()), status=status, status_detail=detail,
            is_default=info.isDefault() or bool(row.get("Default")),
            is_virtual=any(marker in name.lower() for marker in virtual_markers),
            capabilities={
                "color": any(getattr(mode, "name", "") == "Color" for mode in info.supportedColorModes()),
                "duplex": any(getattr(mode, "name", "") != "DuplexNone" for mode in info.supportedDuplexModes()),
                "page_sizes": pages[:20],
                "resolutions": [int(value) for value in info.supportedResolutions()[:20]],
            },
            hardware_id=_safe_text(row.get("PNPDeviceID")), driver=_safe_text(row.get("DriverName")),
        ))
    return devices


def _com_property(properties: Any, names: Iterable[Any], default: Any = "") -> Any:
    for name in names:
        try:
            return properties.Item(name).Value
        except Exception:
            continue
    return default


def detect_scanners() -> tuple[list[Device], str]:
    if os.name != "nt":
        return [], "Skanowanie WIA jest dostępne tylko w Windows."
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return [], "Brakuje składnika pywin32 potrzebnego do obsługi skanera WIA."
    devices: list[Device] = []
    pythoncom.CoInitialize()
    manager = infos = info = device = None
    try:
        manager = win32com.client.Dispatch("WIA.DeviceManager")
        infos = manager.DeviceInfos
        for index in range(1, int(infos.Count) + 1):
            info = infos.Item(index)
            if int(info.Type) != 1:  # ScannerDeviceType
                continue
            props = info.Properties
            name = _safe_text(_com_property(props, ("Name", 7), "Skaner"))
            maker = _safe_text(_com_property(props, ("Manufacturer", 3)))
            model = _safe_text(_com_property(props, ("Description", 4), name))
            device_id = _safe_text(info.DeviceID)
            capabilities = {"flatbed": True, "adf": False, "duplex": False, "wia": True, "twain": False}
            try:
                device = info.Connect()
                handling = int(_com_property(device.Properties, (3086, "Document Handling Capabilities"), 0) or 0)
                capabilities["adf"] = bool(handling & 1)
                capabilities["flatbed"] = bool(handling & 2) or not capabilities["adf"]
                capabilities["duplex"] = bool(handling & 4)
            except Exception:
                pass
            devices.append(Device(
                id=f"scanner:{device_id}", name=name, kind="scanner", manufacturer=maker,
                model=model, connection="przez WIA (typ niepotwierdzony)", status="wykryty",
                status_detail="Dostępność zostanie sprawdzona przy rozpoczęciu skanowania.",
                capabilities=capabilities, hardware_id=device_id, driver="WIA",
            ))
    except Exception as exc:
        return [], f"Windows WIA nie udostępnił listy skanerów ({type(exc).__name__})."
    finally:
        device = info = infos = manager = None
        gc.collect()
        pythoncom.CoUninitialize()
    return devices, "" if devices else "Windows WIA nie wykrył skanera. Sprawdź sterownik producenta."


def _hardware_tokens(value: str) -> set[str]:
    return set(re.findall(r"(?:VID|PID)_[0-9A-F]{4}", value.upper()))


def pair_multifunction_devices(printers: list[Device], scanners: list[Device]) -> None:
    """Łączy funkcje tylko przy wspólnym identyfikatorze VID/PID; nigdy po samej nazwie."""
    for printer in printers:
        p_tokens = _hardware_tokens(printer.hardware_id)
        if not p_tokens:
            continue
        for scanner in scanners:
            s_tokens = _hardware_tokens(scanner.hardware_id)
            if len(p_tokens & s_tokens) >= 2:
                group = "mfp:" + "-".join(sorted(p_tokens & s_tokens))
                printer.group_id = scanner.group_id = group


def discover_devices() -> dict[str, Any]:
    printers = detect_printers()
    scanners, scanner_notice = detect_scanners()
    pair_multifunction_devices(printers, scanners)
    return {
        "printers": [item.to_dict() for item in printers],
        "scanners": [item.to_dict() for item in scanners],
        "scanner_notice": scanner_notice,
    }


def validate_pdf(path: str | Path) -> tuple[Path, int]:
    target = Path(path)
    if not target.is_file():
        raise UserFacingError("Nie znaleziono dokumentu PDF.")
    size = target.stat().st_size
    if size <= 0 or size > MAX_PDF_BYTES:
        raise UserFacingError("Dokument PDF jest pusty albo zbyt duży (limit 100 MB).")
    with target.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise UserFacingError("Wybrany plik nie jest prawidłowym dokumentem PDF.")
    document = QPdfDocument()
    document.load(str(target))
    if document.status() == QPdfDocument.Status.Error or document.pageCount() < 1:
        raise UserFacingError("Nie można otworzyć tego PDF-u albo dokument jest uszkodzony.")
    count = document.pageCount()
    document.close()
    return target, count


def parse_page_range(value: str, total: int) -> list[int]:
    text = value.strip()
    if not text:
        return list(range(total))
    pages: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise UserFacingError("Zakres stron wpisz np. 1-3,5.")
            start, end = int(start_text), int(end_text)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise UserFacingError("Zakres stron wpisz np. 1-3,5.")
        if start < 1 or end < start or end > total:
            raise UserFacingError(f"Dokument ma {total} stron. Popraw wybrany zakres.")
        pages.update(range(start - 1, end))
    if not pages:
        raise UserFacingError("Nie wybrano żadnej strony do wydruku.")
    return sorted(pages)


def print_pdf(path: str, options: dict[str, Any]) -> dict[str, Any]:
    target, total = validate_pdf(path)
    printer_name = _safe_text(options.get("printer"))
    available = set(QPrinterInfo.availablePrinterNames())
    if not printer_name or printer_name not in available:
        raise UserFacingError("Wybrana drukarka nie jest już dostępna. Odśwież listę urządzeń.")
    pages = parse_page_range(_safe_text(options.get("pages")), total)
    copies = max(1, min(99, int(options.get("copies") or 1)))
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPrinterName(printer_name)
    if not printer.isValid():
        raise UserFacingError("Sterownik wybranej drukarki nie jest dostępny.")
    printer.setDocName(target.name)
    printer.setCopyCount(copies)
    printer.setCollateCopies(True)
    printer.setColorMode(QPrinter.ColorMode.Color if options.get("color", True) else QPrinter.ColorMode.GrayScale)
    printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide if options.get("duplex") else QPrinter.DuplexMode.DuplexNone)
    printer.setPageOrientation(
        QPageLayout.Orientation.Landscape if options.get("landscape") else QPageLayout.Orientation.Portrait
    )
    size_id = QPageSize.PageSizeId.Letter if options.get("paper") == "Letter" else QPageSize.PageSizeId.A4
    printer.setPageSize(QPageSize(size_id))

    document = QPdfDocument()
    document.load(str(target))
    painter = QPainter()
    if not painter.begin(printer):
        document.close()
        raise UserFacingError("Nie udało się przekazać dokumentu do kolejki drukowania.")
    try:
        for position, page in enumerate(pages):
            if position and not printer.newPage():
                raise UserFacingError("Drukarka nie przyjęła kolejnej strony dokumentu.")
            rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            points = document.pagePointSize(page)
            scale = min(rect.width() / max(points.width(), 1), rect.height() / max(points.height(), 1))
            target_width = max(1, int(points.width() * scale))
            target_height = max(1, int(points.height() * scale))
            render_scale = min(scale, 300 / 72)
            image_size = QSize(max(1, int(points.width() * render_scale)), max(1, int(points.height() * render_scale)))
            image = document.render(page, image_size)
            if image.isNull():
                raise UserFacingError(f"Nie udało się przygotować strony {page + 1} do wydruku.")
            x = rect.x() + (rect.width() - target_width) // 2
            y = rect.y() + (rect.height() - target_height) // 2
            painter.drawImage(QRect(x, y, target_width, target_height), image)
    finally:
        painter.end()
        document.close()
    return {"printer": printer_name, "pages": [page + 1 for page in pages], "copies": copies, "queued": True}


def scan_wia_pages(device_id: str, source: str = "flatbed") -> list[str]:
    if os.name != "nt":
        raise UserFacingError("Skanowanie WIA jest dostępne tylko w Windows.")
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise UserFacingError("Brakuje składnika obsługi skanera. Zainstaluj wymagania projektu ponownie.") from exc
    pythoncom.CoInitialize()
    created: list[Path] = []
    try:
        manager = win32com.client.Dispatch("WIA.DeviceManager")
        info = manager.DeviceInfos.Item(device_id)
        device = info.Connect()
        if int(device.Items.Count) < 1:
            raise UserFacingError("Skaner nie udostępnił powierzchni skanowania.")
        item = device.Items.Item(1)
        try:
            handling = item.Properties.Item(3088)
            handling.Value = 1 if source == "adf" else 2
        except Exception:
            if source == "adf":
                raise UserFacingError("Sterownik nie udostępnia podajnika ADF.")
        for _page in range(100):
            descriptor, temp_name = tempfile.mkstemp(
                prefix="mailassistant_scan_", suffix=".jpg", dir=str(runtime_cache_dir())
            )
            os.close(descriptor)
            temp_path = Path(temp_name)
            temp_path.unlink(missing_ok=True)  # WIA SaveFile wymaga nowej nazwy
            try:
                image = item.Transfer(JPEG_FORMAT)
                image.SaveFile(str(temp_path))
                if not temp_path.is_file() or temp_path.stat().st_size == 0:
                    raise UserFacingError("Skaner nie zwrócił obrazu.")
                created.append(temp_path)
            except Exception:
                temp_path.unlink(missing_ok=True)
                if source == "adf" and created:
                    break
                raise
            if source != "adf":
                break
        return [str(path) for path in created]
    except Exception as exc:
        for path in created:
            path.unlink(missing_ok=True)
        if isinstance(exc, UserFacingError):
            raise
        message = str(exc).lower()
        if "paper" in message or "empty" in message:
            raise UserFacingError("Podajnik jest pusty albo papier nie został pobrany.") from exc
        if "busy" in message:
            raise UserFacingError("Skaner jest zajęty. Poczekaj chwilę i spróbuj ponownie.") from exc
        raise UserFacingError("Nie udało się pobrać strony ze skanera. Sprawdź zasilanie, połączenie i sterownik WIA.") from exc
    finally:
        pythoncom.CoUninitialize()


def scan_wia_page(device_id: str, source: str = "flatbed") -> str:
    """Zgodny wstecznie skrót zwracający pierwszą zeskanowaną stronę."""
    pages = scan_wia_pages(device_id, source)
    if not pages:
        raise UserFacingError("Skaner nie zwrócił żadnej strony.")
    return pages[0]


def images_to_pdf(image_paths: list[str], output_path: str) -> dict[str, Any]:
    if not image_paths:
        raise UserFacingError("Nie ma żadnej zeskanowanej strony do zapisania.")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    source_size = sum(Path(path).stat().st_size for path in image_paths if Path(path).is_file())
    try:
        ensure_free_space(output.parent, max(source_size * 2, 1024 * 1024))
    except RuntimeError as exc:
        raise UserFacingError(str(exc)) from exc
    temporary = temporary_sibling(output, ".tmp.pdf")
    try:
        writer = QPdfWriter(str(temporary))
        writer.setResolution(150)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setTitle("Skan dokumentu")
        painter = QPainter(writer)
        try:
            for index, image_path in enumerate(image_paths):
                image = QImage(image_path)
                if image.isNull():
                    raise UserFacingError(f"Nie można odczytać zeskanowanej strony {index + 1}.")
                if index:
                    writer.newPage()
                page = writer.pageLayout().paintRectPixels(writer.resolution())
                scaled = image.scaled(page.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                x = page.x() + (page.width() - scaled.width()) // 2
                y = page.y() + (page.height() - scaled.height()) // 2
                painter.drawImage(x, y, scaled)
        finally:
            painter.end()
        validate_pdf(temporary)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": str(output), "pages": len(image_paths)}


def create_test_pdf() -> str:
    descriptor, name = tempfile.mkstemp(
        prefix="mailassistant_test_", suffix=".pdf", dir=str(runtime_cache_dir())
    )
    os.close(descriptor)
    Path(name).unlink(missing_ok=True)
    writer = QPdfWriter(name)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setTitle("Strona testowa Asystenta Poczty")
    painter = QPainter(writer)
    painter.drawText(
        writer.pageLayout().paintRectPixels(writer.resolution()),
        Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
        "ASYSTENT POCZTY\n\nStrona testowa drukarki\n\nJeśli widzisz ten tekst, wydruk działa prawidłowo.",
    )
    painter.end()
    validate_pdf(name)
    return name


def task_device_action(task: dict[str, Any]) -> str:
    text = " ".join(str(task.get(key) or "") for key in ("action", "instruction", "source_quote")).lower()
    normalized = re.sub(r"\s+", " ", text)
    if any(value in normalized for value in ("nie druk", "bez drukowania", "nie trzeba go drukować", "nie trzeba jej drukować")):
        return "none"
    if re.search(r"\b(wydruk|drukuj|wydrukować|egzemplarz papierow)", normalized):
        return "print"
    if re.search(r"\b(zeskan|skanować|skan dokument|utwórz skan)", normalized):
        return "scan"
    return "none"


INSTRUCTIONS = {
    "generic": {
        "title": "Ogólne sprawdzenie urządzenia",
        "steps": [
            "Włącz urządzenie i sprawdź, czy na jego ekranie nie ma komunikatu o błędzie.",
            "Sprawdź przewód USB albo połączenie z tą samą siecią Wi‑Fi.",
            "W Windows otwórz Ustawienia → Bluetooth i urządzenia → Drukarki i skanery.",
            "Jeżeli urządzenia nie ma na liście, zainstaluj pełny sterownik ze strony producenta.",
        ],
        "source": "Ogólna procedura Windows; nie jest instrukcją konkretnego modelu.",
        "version": "1.0", "date": "2026-10-24", "url": "https://support.microsoft.com/windows",
    },
    "brother dcp-j105": {
        "title": "Brother DCP‑J105 — urządzenie offline",
        "steps": [
            "Włącz urządzenie i sprawdź komunikaty na jego wyświetlaczu.",
            "Sprawdź, czy przewód USB jest podłączony bezpośrednio do komputera, a nie przez koncentrator.",
            "W Windows wybierz Brother DCP‑J105 jako drukarkę podczas wydruku.",
            "Jeśli skaner nie jest wykrywany, zainstaluj pełny pakiet sterowników Brother, nie tylko sterownik drukarki.",
        ],
        "source": "Brother — FAQ dla DCP‑J105 i strona instrukcji producenta.",
        "version": "FAQ faq00002646_009 / baza 1.0", "date": "2023-03-27",
        "url": "https://support.brother.com/g/b/faqend.aspx?c=id&faqid=faq00002646_009&ftype3=1972&lang=en&pfs=1&prod=dcpj105_all",
    },
}


def device_instructions(name: str) -> dict[str, Any]:
    key = name.lower()
    for model, data in INSTRUCTIONS.items():
        if model != "generic" and model in key:
            return dict(data, exact_model=True)
    return dict(INSTRUCTIONS["generic"], exact_model=False)
