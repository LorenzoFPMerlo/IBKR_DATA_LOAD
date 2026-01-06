from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ibkr_data_load.config import AppConfig, load_config
from ibkr_data_load.connection import IbkrConnector
from ibkr_data_load.datasets.registry import DatasetRegistry
from ibkr_data_load.logging import build_logger
from ibkr_data_load.planner import build_plan
from ibkr_data_load.execution import HistoricalBarsExecutor
from ibkr_data_load.instruments import InstrumentResolver

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
        return self.run_with_options(instruments, dry_run=dry_run, execute=not dry_run)

    def run_with_options(
        self,
        instruments: Iterable[str],
        identifier_type: str = "ticker",
        dry_run: bool = False,
        execute: bool = False,
        start_date: str | None = None,
        end_date: str | None = None,
        resume: bool = False,
    ) -> dict:
        plan = self.plan(instruments)
        if dry_run or not execute:
            return plan
        logger = build_logger(self.config.logging)
        run_id = plan["run_id"]
        connector = IbkrConnector(config=self.config, run_id=run_id, logger=logger)

        with connector.connect() as ib:
            resolver = InstrumentResolver(config=self.config, run_id=run_id, logger=logger)
            executor = HistoricalBarsExecutor(config=self.config, run_id=run_id, logger=logger)
            for instrument in instruments:
                resolved = resolver.resolve(ib, instrument, identifier_type=identifier_type)
                executor.run(
                    ib,
                    instrument_id=resolved.instrument_id,
                    contract_details=resolved.contract_details,
                    start_date=start_date,
                    end_date=end_date,
                    resume=resume,
                )
        return plan