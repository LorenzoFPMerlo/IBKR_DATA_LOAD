from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def dataset_id(
    con_id: str,
    dataset_name: str,
    parameters: dict[str, Any],
    data_source: str,
    timezone: str,
    schema_version: str,
) -> str:
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