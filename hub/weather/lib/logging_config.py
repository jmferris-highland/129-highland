"""
logging_config.py — Logging setup for the Highland weather daemon.

Configures dual-output logging:
  - Local JSONL file at /var/lib/highland/weather/logs/weather.log
  - Console (stdout) for systemd journal capture

All log records are formatted as JSON for consistency with the
rest of the Highland logging infrastructure.
"""

import json
import logging
import sys
from datetime import datetime, timezone

LOG_PATH = "/var/lib/highland/weather/logs/weather.log"


class JsonlFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "source": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(level: int = logging.INFO) -> None:
    """
    Configure root logger with JSONL file handler and console handler.
    Call once at daemon/script startup.
    """
    formatter = JsonlFormatter()

    # File handler — JSONL
    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setFormatter(formatter)

    # Console handler — for systemd journal
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
