from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from datetime import datetime
import json
from ibkr_data_load.config import AppConfig, load_config
from ibkr_data_load.connection.connector import IbkrConnector
from ibkr_data_load.datasets.registry import DatasetRegistry
from ibkr_data_load.execution import (
    HistoricalBarsExecutor,
    HistoricalTicksExecutor,
    run_market_depth_snapshot,
    run_tick_by_tick_trades_live,
)
from ibkr_data_load.logging import build_logger
from ibkr_data_load.planner import build_plan
from ibkr_data_load.instruments import InstrumentResolver
from ibkr_data_load.options.discovery import (
    is_metadata_fresh,
    load_option_chain_metadata,
    option_chain_metadata_path,
    discover_option_chain,
)
from ibkr_data_load.options.surface_snapshot import build_snapshot_plan, run_option_surface_snapshot

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
        datasets: Iterable[str] | None = None,
        options_snapshot: bool = False,
    ) -> dict:
        selected_datasets = self._resolve_datasets(datasets, options_snapshot)
        plan = self.plan(instruments)
        plan["selected_datasets"] = selected_datasets
        if dry_run or not execute:
            plan["dataset_plans"] = self._build_dataset_plans(
                instruments,
                identifier_type,
                selected_datasets,
                start_date,
                end_date,
            )
            return plan
        logger = build_logger(self.config.logging)
        run_id = plan["run_id"]
        connector = IbkrConnector(config=self.config, run_id=run_id, logger=logger)

        with connector.connect() as ib:
            resolver = InstrumentResolver(config=self.config, run_id=run_id, logger=logger)
            bars_executor = HistoricalBarsExecutor(
                config=self.config, run_id=run_id, logger=logger
            )
            ticks_executor = HistoricalTicksExecutor(
                config=self.config, run_id=run_id, logger=logger
            )
            for instrument in instruments:
                resolved = resolver.resolve(ib, instrument, identifier_type=identifier_type)
                if "historical_bars" in selected_datasets:
                    bars_executor.run(
                        ib,
                        instrument_id=resolved.instrument_id,
                        contract_details=resolved.contract_details,
                        start_date=start_date,
                        end_date=end_date,
                        resume=resume,
                    )

                if "option_chain_discovery" in selected_datasets or (
                    "option_surface_snapshot_daily" in selected_datasets
                ):
                    metadata_path = option_chain_metadata_path(
                        self.config.output_dir, resolved.instrument_id
                    )
                    if not is_metadata_fresh(
                        metadata_path, self.config.options_discovery.cache_max_age_hours
                    ):
                        metadata = discover_option_chain(
                            ib,
                            resolved.contract_details.contract,
                            resolved.instrument_id,
                            logger,
                            self.config,
                        )
                    else:
                        metadata = load_option_chain_metadata(metadata_path)
                    if "option_surface_snapshot_daily" in selected_datasets:
                        if self.config.options_snapshot.enabled:
                            run_option_surface_snapshot(
                                ib,
                                resolved.instrument_id,
                                resolved.contract_details,
                                metadata,
                                self.config,
                                run_id,
                                logger,
                            )
                        else:
                            logger.warning(
                                "Options snapshot disabled in config; skipping instrument=%s",
                                instrument,
                            )

                if "historical_ticks_trades" in selected_datasets:
                    ticks_executor.run(
                        ib,
                        instrument_id=resolved.instrument_id,
                        contract_details=resolved.contract_details,
                        dataset_name="historical_ticks_trades",
                        what_to_show="TRADES",
                        start_date=start_date,
                        end_date=end_date,
                    )

                if "historical_ticks_bid_ask" in selected_datasets:
                    ticks_executor.run(
                        ib,
                        instrument_id=resolved.instrument_id,
                        contract_details=resolved.contract_details,
                        dataset_name="historical_ticks_bid_ask",
                        what_to_show="BID_ASK",
                        start_date=start_date,
                        end_date=end_date,
                    )

                if "tick_by_tick_trades_live" in selected_datasets:
                    run_tick_by_tick_trades_live(
                        ib,
                        resolved.contract_details,
                        duration_seconds=5,
                        logger=logger,
                    )

                if "market_depth" in selected_datasets:
                    run_market_depth_snapshot(
                        ib,
                        resolved.contract_details,
                        num_rows=10,
                        duration_seconds=5,
                        logger=logger,
                    )
        return plan

    def _resolve_datasets(
        self, datasets: Iterable[str] | None, options_snapshot: bool
    ) -> list[str]:
        selected = [value.strip() for value in (datasets or []) if value.strip()]
        if options_snapshot and "option_surface_snapshot_daily" not in selected:
            selected.append("option_surface_snapshot_daily")
        if not selected:
            selected = ["historical_bars"]
        return selected

    def _build_dataset_plans(
        self,
        instruments: Iterable[str],
        identifier_type: str,
        selected_datasets: list[str],
        start_date: str | None,
        end_date: str | None,
    ) -> dict:
        plans: dict[str, dict] = {}
        for instrument in instruments:
            instrument_plan: dict[str, dict] = {}
            instrument_id = self._lookup_cached_instrument_id(
                instrument, identifier_type
            )
            if "option_surface_snapshot_daily" in selected_datasets:
                if instrument_id:
                    metadata_path = option_chain_metadata_path(
                        self.config.output_dir, instrument_id
                    )
                    if metadata_path.exists():
                        metadata = load_option_chain_metadata(metadata_path)
                        plan = build_snapshot_plan(
                            metadata, self.config.options_snapshot, underlying_price=None
                        )
                        instrument_plan["option_surface_snapshot_daily"] = plan
                    else:
                        instrument_plan["option_surface_snapshot_daily"] = {
                            "expiration_count": 0,
                            "strike_count": 0,
                            "estimated_contracts": 0,
                            "estimated_batches": 0,
                            "note": "option_chain_discovery cache not found",
                        }
                else:
                    instrument_plan["option_surface_snapshot_daily"] = {
                        "expiration_count": 0,
                        "strike_count": 0,
                        "estimated_contracts": 0,
                        "estimated_batches": 0,
                        "note": "instrument resolution cache not found",
                    }
            if "historical_ticks_trades" in selected_datasets or "historical_ticks_bid_ask" in selected_datasets:
                plan = self._plan_tick_windows(start_date, end_date)
                instrument_plan["historical_ticks"] = plan
            if instrument_plan:
                plans[instrument] = instrument_plan
        return plans

    def _plan_tick_windows(self, start_date: str | None, end_date: str | None) -> dict:
        settings = self.config.historical_ticks
        start_dt = self._parse_date(start_date or settings.start_date)
        end_dt = self._parse_date(end_date or settings.end_date)
        if not start_dt or not end_dt or end_dt <= start_dt:
            return {"window_count": 0, "chunk_seconds": settings.chunk_seconds}
        total_seconds = (end_dt - start_dt).total_seconds()
        window_count = int(total_seconds // settings.chunk_seconds)
        if total_seconds % settings.chunk_seconds:
            window_count += 1
        return {
            "window_count": window_count,
            "chunk_seconds": settings.chunk_seconds,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }

    def _parse_date(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value)

    def _lookup_cached_instrument_id(
        self, identifier: str, identifier_type: str
    ) -> str | None:
        output_dir = Path(self.config.output_dir)
        for resolution_path in output_dir.rglob("resolution.json"):
            try:
                payload = json.loads(resolution_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if (
                payload.get("identifier") == identifier
                and payload.get("identifier_type") == identifier_type
            ):
                return resolution_path.parent.parent.name
        return None