from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ibkr_data_load.config import AppConfig
from ibkr_data_load.datasets.registry import DatasetRegistry


def build_plan(
    instruments: list[str],
    registry: DatasetRegistry,
    config: AppConfig,
    logger,
) -> dict:
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan = {
        "run_id": run_id,
        "output_dir": config.output_dir,
        "instruments": [],
        "datasets": [],
    }
    for instrument in instruments:
        logger.info("Planning instrument=%s", instrument)
        plan["instruments"].append({"identifier": instrument})
    for dataset in registry.list():
        plan["datasets"].append(
            {
                "name": dataset.name,
                "request_type": dataset.request_type,
                "default_parameters": dataset.default_parameters,
                "status": dataset.status,
                "reason": dataset.reason,
            }
        )
    return plan