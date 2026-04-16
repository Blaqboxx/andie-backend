import os
import shutil
import time
from functools import lru_cache
from pathlib import Path


ARCHIVE_ROOT = Path(os.getenv("ANDIE_ARCHIVE_ROOT", "/mnt/storage"))
ACTIVE_MEMORY_ROOT = Path(os.getenv("ANDIE_ACTIVE_MEMORY_ROOT", "/mnt/nvme/andie/memory"))
ACTIVE_VECTOR_DB_ROOT = Path(os.getenv("ANDIE_VECTOR_DB_ROOT", "/mnt/nvme/andie/vector_db"))
LOCAL_FALLBACK_ROOT = Path(os.getenv("ANDIE_FALLBACK_STORAGE_ROOT", str(Path.cwd() / "storage")))


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _can_write_directory(path: Path) -> bool:
    try:
        _ensure_directory(path)
        probe = path / ".andie_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def get_archive_root() -> Path:
    if _can_write_directory(ARCHIVE_ROOT):
        return ARCHIVE_ROOT
    return _ensure_directory(LOCAL_FALLBACK_ROOT)


def get_log_dir() -> Path:
    return _ensure_directory(get_archive_root() / "logs")


def get_backup_dir() -> Path:
    return _ensure_directory(get_archive_root() / "backups")


def get_archive_dir() -> Path:
    return _ensure_directory(get_archive_root() / "archive")


def ensure_storage_layout() -> dict[str, str]:
    return {
        "archive_root": str(get_archive_root()),
        "logs": str(get_log_dir()),
        "backups": str(get_backup_dir()),
        "archive": str(get_archive_dir()),
        "active_memory": str(_ensure_directory(ACTIVE_MEMORY_ROOT)),
        "active_vector_db": str(_ensure_directory(ACTIVE_VECTOR_DB_ROOT)),
    }


def current_storage_status() -> dict[str, str | bool]:
    resolved_root = get_archive_root()
    return {
        "configured_archive_root": str(ARCHIVE_ROOT),
        "resolved_archive_root": str(resolved_root),
        "using_fallback": resolved_root != ARCHIVE_ROOT,
        "logs": str(get_log_dir()),
        "backups": str(get_backup_dir()),
        "archive": str(get_archive_dir()),
        "active_memory": str(_ensure_directory(ACTIVE_MEMORY_ROOT)),
        "active_vector_db": str(_ensure_directory(ACTIVE_VECTOR_DB_ROOT)),
    }


def backup_memory(source: str | os.PathLike | None = None) -> Path:
    source_path = Path(source) if source else ACTIVE_MEMORY_ROOT
    if not source_path.exists():
        raise FileNotFoundError(f"Active memory path does not exist: {source_path}")

    destination = get_backup_dir() / f"memory_{int(time.time())}"
    shutil.copytree(source_path, destination)
    return destination


def cleanup_logs(retention_days: int = 7) -> list[str]:
    cutoff = time.time() - (retention_days * 24 * 60 * 60)
    deleted_files: list[str] = []

    for entry in get_log_dir().glob("*"):
        if not entry.is_file():
            continue
        if entry.stat().st_mtime >= cutoff:
            continue
        entry.unlink(missing_ok=True)
        deleted_files.append(str(entry))

    return deleted_files