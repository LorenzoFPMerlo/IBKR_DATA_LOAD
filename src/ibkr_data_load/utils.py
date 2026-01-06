from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import pandas as pd
from zoneinfo import ZoneInfo

def dataset_id(
    con_id: str,
    dataset_name: str,
    parameters: dict[str, Any],
    data_source: str,
    timezone: str,
    schema_version: str,
) -> str:
    parameters = normalize_parameters(parameters)
    payload = {
        "con_id": con_id,
        "dataset_name": dataset_name,
        "parameters": parameters,
        "data_source": data_source,
        "timezone": timezone,
        "schema_version": schema_version,
    }
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)

def normalize_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in parameters.items():
        if isinstance(value, dict):
            normalized[key] = normalize_parameters(value)
        elif isinstance(value, list):
            normalized[key] = [normalize_parameters(item) if isinstance(item, dict) else _normalize_value(item) for item in value]
        else:
            normalized[key] = _normalize_value(value)
    return normalized

def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.strip().lower().split())
    return value

def normalize_filename(value: str) -> str:
    safe = value.strip().replace("/", "-").replace(" ", "_")
    return "".join(char for char in safe if char.isalnum() or char in {"_", "-", "."})

def normalize_timestamp(value: Any, timezone_name: str | None) -> pd.Timestamp:
    timestamp = pd.to_datetime(value)
    if timestamp.tzinfo is None:
        tz = ZoneInfo(timezone_name) if timezone_name else ZoneInfo("UTC")
        timestamp = timestamp.tz_localize(tz)
    return timestamp.tz_convert("UTC")