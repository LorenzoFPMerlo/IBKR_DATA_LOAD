from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from ibkr_data_load.config import LoggingConfig


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def build_logger(config: LoggingConfig) -> logging.Logger:
    logger = logging.getLogger("ibkr_data_load")
    if logger.handlers:
        return logger

    level = logging.getLevelName(config.level.upper())
    logger.setLevel(level)

    handler = logging.StreamHandler()
    formatter: logging.Formatter
    formatter = JsonFormatter() if config.json_logs else logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger