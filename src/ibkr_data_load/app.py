from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from pathlib import Path
from typing import Iterable

from ibkr_data_load.checkpoint import load_checkpoint
from ibkr_data_load.config import AppConfig, load_config
from ibkr_data_load.connection.connector import IbkrConnector
from ibkr_data_load.datasets.registry import DatasetRegistry
from ibkr_data_load.execution import (
    HistoricalBarsExecutor,
    HistoricalTicksExecutor,
    OptionHistoricalSelection,
    run_market_depth_snapshot,
    run_option_contract_historical_bars,
    run_tick_by_tick_trades_live,
)
from ibkr_data_load.execution.fundamentals import fetch_fundamentals
from ibkr_data_load.instruments import InstrumentResolver, ResolutionOverrides
from ibkr_data_load.logging import build_logger
from ibkr_data_load.options.discovery import (
    discover_option_chain,
    is_metadata_fresh,
    load_option_chain_metadata,
    option_chain_metadata_path,
)
from ibkr_data_load.options.surface_snapshot import build_snapshot_plan, run_option_surface_snapshot
from ibkr_data_load.planner import build_plan
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
        return build_plan(instruments=list(instruments), registry=self.registry, config=self.config, logger=logger)

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
        debug_ib: bool = False,
        option_expirations_next: int | None = None,
        option_expirations: list[str] | None = None,
        option_strikes: list[float] | None = None,
        option_strikes_nearest: int | None = None,
        option_rights: str = "both",
        option_max_contracts: int | None = None,
        option_bar_size: str | None = None,
        option_duration: str | None = None,
        fundamentals_report_type: str | None = None,
        resolution_currency: str | None = None,
        resolution_primary_exchange: str | None = None,
        resolution_sec_type: str | None = None,
        resolution_exchange: str | None = None,
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
        run_summary = {"run_id": run_id, "start_time": datetime.now(tz=timezone.utc).isoformat(), "end_time": None, "instruments": instruments_list, "datasets": []}
        option_selection = OptionHistoricalSelection(
            expirations_next=option_expirations_next,
            expirations=option_expirations or [],
            strikes=option_strikes,
            strikes_nearest=option_strikes_nearest,
            rights=option_rights,
            max_contracts=option_max_contracts,
            bar_size=option_bar_size,
            duration=option_duration,
        )
        connector = IbkrConnector(config=self.config, run_id=run_id, logger=logger)
        resolution_overrides = ResolutionOverrides(
            sec_type=resolution_sec_type.upper() if resolution_sec_type else None,
            exchange=resolution_exchange.upper() if resolution_exchange else None,
            primary_exchange=resolution_primary_exchange.upper() if resolution_primary_exchange else None,
            currency=resolution_currency.upper() if resolution_currency else None,
        )

        with connector.connect() as ib:
            resolver = InstrumentResolver(config=self.config, run_id=run_id, logger=logger)
            bars_executor = HistoricalBarsExecutor(config=self.config, run_id=run_id, logger=logger, debug_ib=debug_ib)
            ticks_executor = HistoricalTicksExecutor(config=self.config, run_id=run_id, logger=logger)
            for instrument in instruments_list:
                resolved = resolver.resolve(ib, instrument, identifier_type=identifier_type, overrides=resolution_overrides)
                if "historical_bars" in selected_datasets:
                    self._run_job(
                    logger, run_summary, run_id, resolved.instrument_id, "historical_bars",
                    lambda: bars_executor.run(ib, resolved.instrument_id, resolved.contract_details, start_date=start_date, end_date=end_date, resume=resume),
                    )

                metadata = None
                if any(x in selected_datasets for x in ["option_chain_discovery", "option_surface_snapshot_daily", "option_contract_historical_bars"]):
                    metadata_path = option_chain_metadata_path(self.config.output_dir, resolved.instrument_id)
                    if not is_metadata_fresh(metadata_path, self.config.options_discovery.cache_max_age_hours):
                        metadata = discover_option_chain(ib, resolved.contract_details.contract, resolved.instrument_id, logger, self.config)

                    else:
                        metadata = load_option_chain_metadata(metadata_path)
                    
                    if metadata is not None and not metadata.entries:
                        self._append_skipped(run_summary, logger, run_id, resolved.instrument_id, "option_chain_discovery", "no options chain available")
                        metadata = None

                    if self.config.options_snapshot.enabled and metadata is not None:
                        self._run_job(
                            logger, run_summary, run_id, resolved.instrument_id, "option_surface_snapshot_daily",
                            lambda: run_option_surface_snapshot(ib, resolved.instrument_id, resolved.contract_details, metadata, self.config, run_id, logger, resume=resume),
                        )
                    elif metadata is None:
                        self._append_skipped(run_summary, logger, run_id, resolved.instrument_id, "option_surface_snapshot_daily", "no options chain available")

                    else:
                        self._append_skipped(run_summary, logger, run_id, resolved.instrument_id, "option_surface_snapshot_daily", "Options snapshot disabled in config")


                if "option_contract_historical_bars" in selected_datasets and metadata is not None:
                    self._run_job(
                        logger, run_summary, run_id, resolved.instrument_id, "option_contract_historical_bars",
                        lambda: run_option_contract_historical_bars(
                            ib=ib,
                            instrument_id=resolved.instrument_id,
                            underlying_contract_details=resolved.contract_details,
                            metadata=metadata,
                            config=self.config,
                            run_id=run_id,
                            logger=logger,
                            selection=option_selection,
                            start_date=start_date,
                            end_date=end_date,
                            resume=resume,
                        ),
                    )

                if "historical_ticks_trades" in selected_datasets:
                    self._run_job(
                        logger, run_summary, run_id, resolved.instrument_id, "historical_ticks_trades",
                        lambda: ticks_executor.run(ib, resolved.instrument_id, resolved.contract_details, dataset_name="historical_ticks_trades", what_to_show="TRADES", start_date=start_date, end_date=end_date, resume=resume),
                    )

                if "historical_ticks_bid_ask" in selected_datasets:
                    self._run_job(
                        logger, run_summary, run_id, resolved.instrument_id, "historical_ticks_bid_ask",
                        lambda: ticks_executor.run(ib, resolved.instrument_id, resolved.contract_details, dataset_name="historical_ticks_bid_ask", what_to_show="BID_ASK", start_date=start_date, end_date=end_date, resume=resume),
                    )
                
                if "tick_by_tick_trades_live" in selected_datasets:
                    self._run_job(logger, run_summary, run_id, resolved.instrument_id, "tick_by_tick_trades_live", lambda: run_tick_by_tick_trades_live(ib, resolved.contract_details, duration_seconds=5, logger=logger))
                
                if "market_depth" in selected_datasets:
                    self._run_job(logger, run_summary, run_id, resolved.instrument_id, "market_depth", lambda: run_market_depth_snapshot(ib, resolved.contract_details, num_rows=10, duration_seconds=5, logger=logger))
                
                if "fundamentals_report" in selected_datasets:
                    if self.config.fundamentals.enabled:
                        dataset_def = self.registry.get("fundamentals_report")
                        self._run_job(
                            logger, run_summary, run_id, resolved.instrument_id, "fundamentals_report",
                            lambda: fetch_fundamentals(ib=ib, contract=resolved.contract_details.contract, dataset_def=dataset_def, config=self.config, logger=logger, run_id=run_id, instrument_id=resolved.instrument_id, report_type_override=fundamentals_report_type),
                        )
                    else:
                        self._append_skipped(run_summary, logger, run_id, resolved.instrument_id, "fundamentals_report", "Fundamentals disabled in config")

        run_summary["end_time"] = datetime.now(tz=timezone.utc).isoformat()
        summary_path = Path(self.config.output_dir) / "meta" / "runs" / run_id / "run_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(summary_path, json.dumps(run_summary, indent=2))
        return plan

    def _append_skipped(self, run_summary: dict, logger, run_id: str, instrument_id: str, dataset_name: str, reason: str) -> None:
        self._log_job_status(logger, run_id, instrument_id, dataset_name, "skipped", error_message=reason)
        run_summary["datasets"].append({"instrument_id": instrument_id, "dataset_name": dataset_name, "status": "skipped", "error_message": reason})

    def _resolve_datasets(self, datasets: Iterable[str] | None, options_snapshot: bool) -> list[str]:
        selected = [value.strip() for value in (datasets or []) if value.strip()]
        if options_snapshot and "option_surface_snapshot_daily" not in selected:
            selected.append("option_surface_snapshot_daily")
        if not selected:
            selected = ["historical_bars"]
        return selected

    def _build_dataset_plans(self, instruments: Iterable[str], identifier_type: str, selected_datasets: list[str], start_date: str | None, end_date: str | None, fundamentals_report_type: str | None) -> dict:
        plans: dict[str, dict] = {}
        for instrument in instruments:
            instrument_plan: dict[str, dict] = {}
            instrument_id = self._lookup_cached_instrument_id(instrument, identifier_type)
            if "option_surface_snapshot_daily" in selected_datasets:
                if instrument_id:
                    metadata_path = option_chain_metadata_path(self.config.output_dir, instrument_id)
                    if metadata_path.exists():
                        metadata = load_option_chain_metadata(metadata_path)
                        instrument_plan["option_surface_snapshot_daily"] = build_snapshot_plan(metadata, self.config.options_snapshot, underlying_price=None)
                    else:
                        instrument_plan["option_surface_snapshot_daily"] = {"note": "option_chain_discovery cache not found"}
                else:
                    instrument_plan["option_surface_snapshot_daily"] = {"note": "instrument resolution cache not found"}
            if "historical_ticks_trades" in selected_datasets or "historical_ticks_bid_ask" in selected_datasets:
                instrument_plan["historical_ticks"] = self._plan_tick_windows(start_date, end_date)
            if "fundamentals_report" in selected_datasets:
                instrument_plan["fundamentals_report"] = {"reportType": fundamentals_report_type or self.config.fundamentals.default_report_type}
            if "option_contract_historical_bars" in selected_datasets:
                instrument_plan["option_contract_historical_bars"] = {"note": "will resolve from option chain metadata at execution time"}
            if instrument_plan:
                plans[instrument] = instrument_plan
        return plans
    
    def _log_job_status(self, logger, run_id: str, instrument_id: str, dataset_name: str, status: str, stats: dict | None = None, error_message: str | None = None) -> None:
        payload = {"run_id": run_id, "instrument_id": instrument_id, "dataset_name": dataset_name, "status": status}
        if stats is not None:
            payload["stats"] = stats
        if error_message:
            payload["error_message"] = error_message
        logger.info("Job status update", extra=payload)

    def _run_job(self, logger, run_summary: dict, run_id: str, instrument_id: str, dataset_name: str, runner) -> None:
        start = time.monotonic()
        self._log_job_status(logger, run_id, instrument_id, dataset_name, "started")
        try:
            result = runner()
            duration = time.monotonic() - start
            stats = {"duration_seconds": round(duration, 3)}
            if hasattr(result, "row_count"):
                stats["rows_written"] = result.row_count
            if hasattr(result, "dataset_id"):
                stats["dataset_id"] = result.dataset_id
            self._write_job_result(instrument_id, dataset_name, result, stats)
            self._log_job_status(logger, run_id, instrument_id, dataset_name, "succeeded", stats=stats)
            run_summary["datasets"].append({"instrument_id": instrument_id, "dataset_name": dataset_name, "status": "succeeded", "stats": stats})
        except Exception as exc:
            duration = time.monotonic() - start
            self._log_job_status(logger, run_id, instrument_id, dataset_name, "failed", stats={"duration_seconds": round(duration, 3)}, error_message=str(exc))
            run_summary["datasets"].append({"instrument_id": instrument_id, "dataset_name": dataset_name, "status": "failed", "error_message": str(exc)})

    def _write_job_result(self, instrument_id: str, dataset_name: str, result, stats: dict) -> None:
        dataset = stats.get("dataset_id")
        if not dataset:
            return
        checkpoint_path = Path(self.config.output_dir) / instrument_id / "meta" / "checkpoints" / f"{dataset}.json"
        checkpoint_used = False
        if checkpoint_path.exists():
            cp = load_checkpoint(checkpoint_path)
            checkpoint_used = cp is not None
        payload = {
            "dataset_name": dataset_name,
            "dataset_id": dataset,
            "parameters_used": getattr(result, "parameters_used", {}),
            "chunks_attempted": getattr(result, "chunks_attempted", None),
            "chunks_succeeded": getattr(result, "chunks_succeeded", None),
            "chunks_failed": getattr(result, "chunks_failed", None),
            "rows_written": stats.get("rows_written"),
            "elapsed_seconds": stats.get("duration_seconds"),
            "checkpoint_used": getattr(result, "checkpoint_used", checkpoint_used),
            "final_status": "succeeded",
        }
        path = Path(self.config.output_dir) / instrument_id / "meta" / "execution" / dataset / "job_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(payload, indent=2))

    
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
        return {"window_count": window_count, "chunk_seconds": settings.chunk_seconds, "start": start_dt.isoformat(), "end": end_dt.isoformat()}

    def _parse_date(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value)

    def _lookup_cached_instrument_id(self, identifier: str, identifier_type: str) -> str | None:
        output_dir = Path(self.config.output_dir)
        for resolution_path in output_dir.rglob("resolution.json"):
            try:
                payload = json.loads(resolution_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            payload_input = payload.get("input", {})
            resolved_identifier = payload.get("identifier", payload_input.get("identifier"))
            resolved_identifier_type = payload.get("identifier_type", payload_input.get("identifier_type"))
            if resolved_identifier == identifier and resolved_identifier_type == identifier_type:
                return resolution_path.parent.parent.name
        return None