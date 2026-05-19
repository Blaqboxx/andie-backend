import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .storage import get_log_dir


LOG_FORMAT = "[%(asctime)s] %(name)s %(levelname)s: %(message)s"


def _logger_filename(name: str) -> str:
    slug = name.lower().replace(" ", "_")
    return f"{slug}.log"


def _has_stream_handler(logger: logging.Logger) -> bool:
    return any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, RotatingFileHandler)
        for handler in logger.handlers
    )


def _has_file_handler(logger: logging.Logger, log_path: Path) -> bool:
    return any(getattr(handler, "baseFilename", None) == str(log_path) for handler in logger.handlers)


def log_event(filename: str, content: str) -> Path:
    log_path = get_log_dir() / filename
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(content + "\n")
    return log_path


class Logger:
    def __init__(self, name: str, level: int = logging.INFO):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False

        formatter = logging.Formatter(LOG_FORMAT)
        if not _has_stream_handler(self.logger):
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(formatter)
            self.logger.addHandler(stream_handler)

        log_path = get_log_dir() / _logger_filename(name)
        if not _has_file_handler(self.logger, log_path):
            file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

    def debug(self, msg):
        self.logger.debug(msg)

    def info(self, msg):
        self.logger.info(msg)

    def warning(self, msg):
        self.logger.warning(msg)

    def error(self, msg):
        self.logger.error(msg)
