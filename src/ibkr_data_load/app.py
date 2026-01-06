from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ibkr_data_load.config import AppConfig, load_config
from ibkr_data_load.datasets.registry import DatasetRegistry
from ibkr_data_load.logging import build_logger
from ibkr_data_load.planner import build_plan


@dataclass
class LoadApp:
    config: AppConfig
    registry: DatasetRegistry

    @classmethod
    def from_paths(
        cls,
        defaults_path: Path | str = "config/defaults.yaml",
        user_path: Path | str = "config/user.yaml",
    ) -> "LoadApp":
        config = load_config(Path(defaults_path), Path(user_path))
        registry = DatasetRegistry.default()
        return cls(config=config, registry=registry)

    def plan(self, instruments: Iterable[str]) -> dict:
        logger = build_logger(self.config.logging)
        plan = build_plan(
            instruments=list(instruments),
            registry=self.registry,
            config=self.config,
            logger=logger,
        )
        return plan

    def run(self, instruments: Iterable[str], dry_run: bool = False) -> dict:
        plan = self.plan(instruments)
        if dry_run:
            return plan
        raise NotImplementedError(
            "Execution engine is not implemented yet. Use --dry-run to generate a plan."
        )