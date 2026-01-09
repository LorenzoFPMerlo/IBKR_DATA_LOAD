from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from datetime import datetime, timezone
import time
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
from ibkr_data_load.execution.fundamentals import fetch_fundamentals
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
from ibkr_data_load.utils import atomic_write

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
        fundamentals_report_type: str | None = None,
    ) -> dict:
        instruments_list = list(instruments)
        selected_datasets = self._resolve_datasets(datasets, options_snapshot)
        plan = self.plan(instruments_list)
        plan["selected_datasets"] = selected_datasets
        if dry_run or not execute:
            plan["dataset_plans"] = self._build_dataset_plans(
                instruments_list,
                identifier_type,
                selected_datasets,
                start_date,
                end_date,
                fundamentals_report_type,
            )
            return plan
        logger = build_logger(self.config.logging)
        run_id = plan["run_id"]
        run_summary = {
            "run_id": run_id,
            "start_time": datetime.now(tz=timezone.utc).isoformat(),
            "end_time": None,
            "instruments": instruments_list,
            "datasets": [],
        }
        logger.info(
            "Run started",
            extra={
                "run_id": run_id,
                "status": "started",
                "datasets": selected_datasets,
                "instrument_count": len(instruments_list),
            },
        )
        connector = IbkrConnector(config=self.config, run_id=run_id, logger=logger)

        with connector.connect() as ib:
            resolver = InstrumentResolver(config=self.config, run_id=run_id, logger=logger)
            bars_executor = HistoricalBarsExecutor(
                config=self.config, run_id=run_id, logger=logger
            )
            ticks_executor = HistoricalTicksExecutor(
                config=self.config, run_id=run_id, logger=logger
            )
            for instrument in instruments_list:
                resolved = resolver.resolve(ib, instrument, identifier_type=identifier_type)
                if "historical_bars" in selected_datasets:
                    self._run_job(
                        logger,
                        run_summary,
                        run_id,
                        resolved.instrument_id,
                        "historical_bars",
                        lambda: bars_executor.run(
                            ib,
                            instrument_id=resolved.instrument_id,
                            contract_details=resolved.contract_details,
                            start_date=start_date,
                            end_date=end_date,
                            resume=resume,
                        ),
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
                            self._run_job(
                                logger,
                                run_summary,
                                run_id,
                                resolved.instrument_id,
                                "option_surface_snapshot_daily",
                                lambda: run_option_surface_snapshot(
                                    ib,
                                    resolved.instrument_id,
                                    resolved.contract_details,
                                    metadata,
                                    self.config,
                                    run_id,
                                    logger,
                                    resume=resume,
                                ),
                            )
                        else:
                            self._log_job_status(
                                logger,
                                run_id,
                                resolved.instrument_id,
                                "option_surface_snapshot_daily",
                                "skipped",
                                error_message="Options snapshot disabled in config",
                            )
                            run_summary["datasets"].append(
                                {
                                    "instrument_id": resolved.instrument_id,
                                    "dataset_name": "option_surface_snapshot_daily",
                                    "status": "skipped",
                                    "error_message": "Options snapshot disabled in config",
                                }
                            )

                if "historical_ticks_trades" in selected_datasets:
                    self._run_job(
                        logger,
                        run_summary,
                        run_id,
                        resolved.instrument_id,
                        "historical_ticks_trades",
                        lambda: ticks_executor.run(
                            ib,
                            instrument_id=resolved.instrument_id,
                            contract_details=resolved.contract_details,
                            dataset_name="historical_ticks_trades",
                            what_to_show="TRADES",
                            start_date=start_date,
                            end_date=end_date,
                            resume=resume,
                        ),
                    )

                if "historical_ticks_bid_ask" in selected_datasets:
                    self._run_job(
                        logger,
                        run_summary,
                        run_id,
                        resolved.instrument_id,
                        "historical_ticks_bid_ask",
                        lambda: ticks_executor.run(
                            ib,
                            instrument_id=resolved.instrument_id,
                            contract_details=resolved.contract_details,
                            dataset_name="historical_ticks_bid_ask",
                            what_to_show="BID_ASK",
                            start_date=start_date,
                            end_date=end_date,
                            resume=resume,
                        ),
                    )

                if "tick_by_tick_trades_live" in selected_datasets:
                    self._run_job(
                        logger,
                        run_summary,
                        run_id,
                        resolved.instrument_id,
                        "tick_by_tick_trades_live",
                        lambda: run_tick_by_tick_trades_live(
                            ib,
                            resolved.contract_details,
                            duration_seconds=5,
                            logger=logger,
                        ),
                    )

                if "market_depth" in selected_datasets:
                    self._run_job(
                        logger,
                        run_summary,
                        run_id,
                        resolved.instrument_id,
                        "market_depth",
                        lambda: run_market_depth_snapshot(
                            ib,
                            resolved.contract_details,
                            num_rows=10,
                            duration_seconds=5,
                            logger=logger,
                        ),
                    )

                if "fundamentals_report" in selected_datasets:
                    dataset_def = self.registry.get("fundamentals_report")
                    if self.config.fundamentals.enabled:
                        self._run_job(
                            logger,
                            run_summary,
                            run_id,
                            resolved.instrument_id,
                            "fundamentals_report",
                            lambda: fetch_fundamentals(
                                ib=ib,
                                contract=resolved.contract_details.contract,
                                dataset_def=dataset_def,
                                config=self.config,
                                logger=logger,
                                run_id=run_id,
                                instrument_id=resolved.instrument_id,
                                report_type_override=fundamentals_report_type,
                            ),
                        )
                    else:
                        self._log_job_status(
                            logger,
                            run_id,
                            resolved.instrument_id,
                            "fundamentals_report",
                            "skipped",
                            error_message="Fundamentals disabled in config",
                        )
                        run_summary["datasets"].append(
                            {
                                "instrument_id": resolved.instrument_id,
                                "dataset_name": "fundamentals_report",
                                "status": "skipped",
                                "error_message": "Fundamentals disabled in config",
                            }
                        )
        run_summary["end_time"] = datetime.now(tz=timezone.utc).isoformat()
        summary_path = (
            Path(self.config.output_dir)
            / "meta"
            / "runs"
            / run_id
            / "run_summary.json"
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_payload = json.dumps(run_summary, indent=2)
        atomic_write(summary_path, summary_payload)
        logger.info(
            "Run completed",
            extra={
                "run_id": run_id,
                "status": "completed",
                "dataset_count": len(run_summary["datasets"]),
            },
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
        fundamentals_report_type: str | None,
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
            if "fundamentals_report" in selected_datasets:
                instrument_plan["fundamentals_report"] = {
                    "reportType": fundamentals_report_type
                    or self.config.fundamentals.default_report_type
                }
            if instrument_plan:
                plans[instrument] = instrument_plan
        return plans
    
    def _log_job_status(
        self,
        logger,
        run_id: str,
        instrument_id: str,
        dataset_name: str,
        status: str,
        stats: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        payload = {
            "run_id": run_id,
            "instrument_id": instrument_id,
            "dataset_name": dataset_name,
            "status": status,
        }
        if stats is not None:
            payload["stats"] = stats
        if error_message:
            payload["error_message"] = error_message
        logger.info("Job status update", extra=payload)

    def _run_job(
        self,
        logger,
        run_summary: dict,
        run_id: str,
        instrument_id: str,
        dataset_name: str,
        runner,
    ) -> None:
        start = time.monotonic()
        self._log_job_status(logger, run_id, instrument_id, dataset_name, "started")
        try:
            result = runner()
            duration = time.monotonic() - start
            stats = {
                "duration_seconds": round(duration, 3),
            }
            if hasattr(result, "row_count"):
                stats["rows_written"] = result.row_count
            if hasattr(result, "dataset_id"):
                stats["dataset_id"] = result.dataset_id
            self._log_job_status(
                logger, run_id, instrument_id, dataset_name, "succeeded", stats=stats
            )
            run_summary["datasets"].append(
                {
                    "instrument_id": instrument_id,
                    "dataset_name": dataset_name,
                    "status": "succeeded",
                    "stats": stats,
                }
            )
        except Exception as exc:
            duration = time.monotonic() - start
            self._log_job_status(
                logger,
                run_id,
                instrument_id,
                dataset_name,
                "failed",
                stats={"duration_seconds": round(duration, 3)},
                error_message=str(exc),
            )
            run_summary["datasets"].append(
                {
                    "instrument_id": instrument_id,
                    "dataset_name": dataset_name,
                    "status": "failed",
                    "error_message": str(exc),
                }
            )
    
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