"""Loglama: konsol + dosya; API anahtarı benzeri değerleri maskeler."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def redact(text: str) -> str:
    for var in _SECRET_ENV_VARS:
        value = os.environ.get(var)
        if value and len(value) >= 8:
            text = text.replace(value, "***")
    return _KEY_PATTERN.sub("sk-***", text)


class RedactingFilter(logging.Filter):
    _fmt = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:  # hata izleri (traceback) de maskelenir
            record.exc_text = redact(self._fmt.formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def setup_logging(log_dir: Path, verbose: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("umbrella_vea")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    console.addFilter(RedactingFilter())

    file_handler = logging.FileHandler(log_dir / "vea.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactingFilter())

    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger
