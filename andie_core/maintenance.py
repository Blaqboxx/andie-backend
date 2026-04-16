import os
import threading
import time
from datetime import datetime, timezone

from andie_core.logger import Logger
from andie_core.storage import backup_memory, cleanup_logs, ensure_storage_layout


class MaintenanceScheduler:
    def __init__(self, backup_interval_seconds: int | None = None, cleanup_interval_seconds: int | None = None, retention_days: int | None = None):
        self.backup_interval_seconds = backup_interval_seconds or int(os.getenv("ANDIE_BACKUP_INTERVAL_SECONDS", str(6 * 60 * 60)))
        self.cleanup_interval_seconds = cleanup_interval_seconds or int(os.getenv("ANDIE_CLEANUP_INTERVAL_SECONDS", str(60 * 60)))
        self.retention_days = retention_days or int(os.getenv("ANDIE_LOG_RETENTION_DAYS", "7"))
        self.logger = Logger("MaintenanceScheduler")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_backup_at: str | None = None
        self._last_backup_destination: str | None = None
        self._last_cleanup_at: str | None = None
        self._last_cleanup_count = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        ensure_storage_layout()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="andie-maintenance", daemon=True)
        self._thread.start()
        self.logger.info("Background maintenance scheduler started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self.logger.info("Background maintenance scheduler stopped")

    def status(self) -> dict:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "backup_interval_seconds": self.backup_interval_seconds,
                "cleanup_interval_seconds": self.cleanup_interval_seconds,
                "retention_days": self.retention_days,
                "last_backup_at": self._last_backup_at,
                "last_backup_destination": self._last_backup_destination,
                "last_cleanup_at": self._last_cleanup_at,
                "last_cleanup_count": self._last_cleanup_count,
            }

    def trigger_backup(self) -> str:
        destination = str(backup_memory())
        with self._lock:
            self._last_backup_at = self._timestamp()
            self._last_backup_destination = destination
        self.logger.info(f"Memory backup created at {destination}")
        return destination

    def trigger_cleanup(self) -> int:
        deleted_files = cleanup_logs(self.retention_days)
        with self._lock:
            self._last_cleanup_at = self._timestamp()
            self._last_cleanup_count = len(deleted_files)
        self.logger.info(f"Log cleanup removed {len(deleted_files)} files")
        return len(deleted_files)

    def _run_loop(self) -> None:
        next_backup = time.time() + self.backup_interval_seconds
        next_cleanup = time.time() + self.cleanup_interval_seconds

        while not self._stop_event.wait(timeout=5):
            now = time.time()

            if now >= next_backup:
                try:
                    self.trigger_backup()
                except Exception as exc:
                    self.logger.error(f"Scheduled memory backup failed: {exc}")
                next_backup = now + self.backup_interval_seconds

            if now >= next_cleanup:
                try:
                    self.trigger_cleanup()
                except Exception as exc:
                    self.logger.error(f"Scheduled log cleanup failed: {exc}")
                next_cleanup = now + self.cleanup_interval_seconds

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()