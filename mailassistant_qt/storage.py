"""Bezpieczne, atomowe operacje zapisu używane przez aplikację."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


MAX_DOCUMENT_BYTES = 100 * 1024 * 1024
MIN_FREE_RESERVE = 10 * 1024 * 1024


class StorageError(RuntimeError):
    pass


def ensure_free_space(directory: str | Path, required: int, reserve: int = MIN_FREE_RESERVE) -> None:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(target).free
    except OSError as exc:
        raise StorageError("Nie można sprawdzić wolnego miejsca w wybranym katalogu.") from exc
    if free < max(0, int(required)) + max(0, int(reserve)):
        raise StorageError("Brakuje miejsca na dysku. Wybierz inny katalog albo zwolnij miejsce.")


def atomic_write_bytes(path: str | Path, data: bytes, mode: int = 0o600) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ensure_free_space(target.parent, len(data))
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass
        os.replace(temp_path, target)
        return target
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def temporary_sibling(path: str | Path, suffix: str = ".tmp") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=suffix, dir=str(target.parent)
    )
    os.close(descriptor)
    temp_path = Path(temporary)
    temp_path.unlink(missing_ok=True)
    return temp_path
