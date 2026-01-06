from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ibkr_data_load.utils import atomic_write


@dataclass
class Checkpoint:
    dataset_id: str
    watermark: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "dataset_id": self.dataset_id,
                "watermark": self.watermark,
                "payload": self.payload,
            },
            indent=2,
        )


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    atomic_write(path, checkpoint.to_json())


def load_checkpoint(path: Path) -> Checkpoint | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Checkpoint(
        dataset_id=data["dataset_id"],
        watermark=data["watermark"],
        payload=data.get("payload", {}),
    )