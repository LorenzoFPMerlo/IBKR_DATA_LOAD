from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
from ib_insync import ContractDetails, IB

from ibkr_data_load.checkpoint import Checkpoint, load_checkpoint, save_checkpoint
from ibkr_data_load.config import AppConfig
from ibkr_data_load.dqc import run_bars_dqc, write_bars_dqc
from ibkr_data_load.retry_backoff import build_retryer
from ibkr_data_load.utils import atomic_write, dataset_id, normalize_parameters, normalize_timestamp

SCHEMA_VERSION = "1"
DATA_SOURCE = "ibkr"
_ALLOWED_DURATION_UNITS = {"S", "D", "W", "M", "Y"}
_ALLOWED_BAR_UNITS = {"secs", "min", "mins", "hour", "hours", "day", "week", "month"}


@dataclass
class HistoricalBarsResult:
    dataset_id: str
    output_path: Path
    row_count: int
    chunks_attempted: int = 0
    chunks_succeeded: int = 0
    chunks_failed: int = 0
    checkpoint_used: bool = False
    parameters_used: dict | None = None


class HistoricalBarsExecutor:
    def __init__(self, config: AppConfig, run_id: str, logger=None, debug_ib: bool = False) -> None:
        self.config = config
        self.run_id = run_id
        self.logger = logger
        self.debug_ib = debug_ib
        self._last_request_at: float | None = None
        self._ib_messages: list[str] = []

    def run(
        self,
        ib: IB,
        instrument_id: str,
        contract_details: ContractDetails,
        start_date: str | None = None,
        end_date: str | None = None,
        resume: bool = False,
    ) -> HistoricalBarsResult:
        settings = self.config.historical_bars
        if not settings.enabled:
            return HistoricalBarsResult(dataset_id="", output_path=Path(""), row_count=0)

        self._validate_request_inputs(settings.bar_size, settings.chunk_duration)
        what_to_show_candidates = self._what_to_show_candidates(contract_details)
        active_what = what_to_show_candidates[0]

        params = normalize_parameters({"barSize": settings.bar_size, "whatToShow": active_what, "useRTH": settings.use_rth})
        dataset = dataset_id(
            con_id=str(contract_details.contract.conId),
            dataset_name="historical_bars",
            parameters=params,
            data_source=DATA_SOURCE,
            timezone="UTC",
            schema_version=SCHEMA_VERSION,
        )

        output_path = Path(self.config.output_dir) / instrument_id / "market" / "historical" / f"historical_bars__{dataset}.csv"
        checkpoint_path = Path(self.config.output_dir) / instrument_id / "meta" / "checkpoints" / f"{dataset}.json"

        start_dt = self._parse_date(start_date or settings.start_date)
        end_dt = self._parse_date(end_date or settings.end_date) or datetime.now(tz=timezone.utc)

        existing_last = self._existing_last_timestamp(output_path)
        if existing_last and not resume:
            overlap = self._bar_delta(settings.bar_size) * settings.overlap_bars
            overlap_start = existing_last - overlap
            if start_dt is None or overlap_start > start_dt:
                start_dt = overlap_start

        checkpoint_used = False
        if resume:
            checkpoint = load_checkpoint(checkpoint_path)
            if checkpoint:
                checkpoint_used = True
                end_dt = datetime.fromisoformat(checkpoint.watermark)
                if settings.overlap_bars > 0:
                    end_dt = end_dt - self._bar_delta(settings.bar_size) * settings.overlap_bars

        chunk_duration = settings.chunk_duration
        expected_delta = self._bar_delta(settings.bar_size)
        all_new = []
        chunks_attempted = chunks_succeeded = chunks_failed = 0

        current_end = end_dt
        while start_dt is None or current_end > start_dt:
            chunks_attempted += 1
            bars, active_what, err = self._fetch_chunk_with_fallback(ib, contract_details, current_end, chunk_duration, what_to_show_candidates)
            if err is not None:
                chunks_failed += 1
                if self.logger:
                    self.logger.warning("historical bars chunk failed conId=%s error=%s", contract_details.contract.conId, err)
                break

            if not bars:
                chunks_failed += 1
                if self.logger:
                    self.logger.info("historical bars returned no data conId=%s end=%s", contract_details.contract.conId, current_end.isoformat())
                break

            df = self._bars_to_frame(bars, contract_details, dataset)
            if start_dt is not None:
                df = df[df["time"] >= pd.Timestamp(start_dt)]
            if df.empty:
                break
            all_new.append(df)
            earliest = df["time"].min().to_pydatetime()
            save_checkpoint(checkpoint_path, Checkpoint(dataset_id=dataset, watermark=earliest.isoformat(), payload={"whatToShow": active_what}))
            current_end = earliest - expected_delta

        if all_new:
            combined_new = pd.concat(all_new, ignore_index=True)
        else:
            combined_new = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume", "wap", "barCount", "conId", "dataset_id"])

        merged = self._merge_append(output_path, combined_new, dataset)
        self._write_csv(output_path, merged)

        dqc_result = run_bars_dqc(merged, time_column="time", key_columns=["time", "conId", "dataset_id"], expected_delta=expected_delta)
        write_bars_dqc(dqc_result, output_dir=Path(self.config.output_dir) / instrument_id / "meta" / "dqc" / dataset)

        self._persist_effective_params(instrument_id, contract_details.contract.conId, settings.bar_size, active_what, settings.use_rth)
        if self.debug_ib:
            self._write_debug_bundle(instrument_id)

        return HistoricalBarsResult(
            dataset_id=dataset,
            output_path=output_path,
            row_count=len(merged),
            chunks_attempted=chunks_attempted,
            chunks_succeeded=chunks_succeeded,
            chunks_failed=chunks_failed,
            checkpoint_used=checkpoint_used,
            parameters_used={"barSize": settings.bar_size, "duration": settings.chunk_duration, "whatToShow": active_what, "useRTH": settings.use_rth},
        )
    
    def _fetch_chunk_with_fallback(self, ib: IB, contract_details: ContractDetails, end_dt: datetime, duration: str, what_to_show_candidates: list[str]):
        last_error: Exception | None = None
        for what in what_to_show_candidates:
            bars, err = self._fetch_chunk(ib, contract_details, end_dt, duration, what)
            if err is None and bars:
                return bars, what, None
            if err:
                last_error = err
                self._write_last_error(err)
            if not err and not bars:
                # no data for this whatToShow, try fallback
                continue
        return [], what_to_show_candidates[0], last_error

    def _fetch_chunk(self, ib: IB, contract_details: ContractDetails, end_dt: datetime, duration: str, what_to_show: str):
        retryer = build_retryer(
            self.config.pacing,
            self.logger,
            self.run_id,
            context={"dataset": "historical_bars", "conId": contract_details.contract.conId, "whatToShow": what_to_show},
        )
        try:
            for attempt in retryer:
                with attempt:
                    self._apply_pacing(self.config.historical_bars.min_request_interval_seconds)
                    request_payload = {
                        "conId": contract_details.contract.conId,
                        "symbol": contract_details.contract.symbol,
                        "secType": contract_details.contract.secType,
                        "exchange": contract_details.contract.exchange,
                        "endDateTime": end_dt.isoformat(),
                        "durationStr": duration,
                        "barSizeSetting": self.config.historical_bars.bar_size,
                        "whatToShow": what_to_show,
                        "useRTH": self.config.historical_bars.use_rth,
                        "formatDate": self.config.historical_bars.format_date,
                    }
                    self._write_last_request(request_payload)
                    bars = ib.reqHistoricalData(
                        contract_details.contract,
                        endDateTime=end_dt,
                        durationStr=duration,
                        barSizeSetting=self.config.historical_bars.bar_size,
                        whatToShow=what_to_show,
                        useRTH=self.config.historical_bars.use_rth,
                        formatDate=self.config.historical_bars.format_date,
                    )
                    self._last_request_at = time.time()
                    return bars, None
        except Exception as exc:
            self._ib_messages.append(str(exc))
            return [], exc
        return [], RuntimeError("reqHistoricalData failed without explicit exception")

    def _what_to_show_candidates(self, contract_details: ContractDetails) -> list[str]:
        primary = self.config.historical_bars.what_to_show
        sec_type = (contract_details.contract.secType or "").upper()
        if sec_type == "IND":
            defaults = [primary, "MIDPOINT", "TRADES"]
        else:
            defaults = [primary, "TRADES", "MIDPOINT"]
        ordered: list[str] = []
        for value in defaults:
            if value and value not in ordered:
                ordered.append(value)
        return ordered

    def _validate_request_inputs(self, bar_size: str, duration: str) -> None:
        bar_chunks = bar_size.split(maxsplit=1)
        if len(bar_chunks) != 2 or bar_chunks[1].lower() not in _ALLOWED_BAR_UNITS:
            raise ValueError(f"Invalid bar size '{bar_size}'. Expected format like '1 min' or '1 day'.")
        d_chunks = duration.split(maxsplit=1)
        if len(d_chunks) != 2 or d_chunks[1].upper() not in _ALLOWED_DURATION_UNITS:
            raise ValueError(f"Invalid duration '{duration}'. Expected format like '10 D' or '1 Y'.")

    def _persist_effective_params(self, instrument_id: str, con_id: int, bar_size: str, what_to_show: str, use_rth: int) -> None:
        payload = {
            "conId": con_id,
            "barSize": bar_size,
            "whatToShow": what_to_show,
            "useRTH": use_rth,
            "updated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        }
        path = Path(self.config.output_dir) / instrument_id / "meta" / "historical_bars_effective_params.json"
        atomic_write(path, json.dumps(payload, indent=2))

    def _debug_dir(self) -> Path:
        return Path(self.config.output_dir) / "meta" / "runs" / self.run_id / "debug"

    def _write_last_request(self, payload: dict) -> None:
        if not self.debug_ib:
            return
        debug_dir = self._debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(debug_dir / "last_request_params.json", json.dumps(payload, indent=2))

    def _write_last_error(self, exc: Exception) -> None:
        if not self.debug_ib:
            return
        debug_dir = self._debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        payload = {"error": str(exc), "timestamp_utc": datetime.now(tz=timezone.utc).isoformat()}
        atomic_write(debug_dir / "last_error.json", json.dumps(payload, indent=2))

    def _write_debug_bundle(self, instrument_id: str) -> None:
        debug_dir = self._debug_dir()
        debug_dir.mkdir(parents=True, exist_ok=True)
        tail = "\n".join(self._ib_messages[-200:])
        atomic_write(debug_dir / "ib_messages_tail.log", tail)

    def _apply_pacing(self, min_interval: float) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.time() - self._last_request_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    def _bars_to_frame(self, bars: Iterable, contract_details: ContractDetails, dataset: str) -> pd.DataFrame:
        rows = []
        for bar in bars:
            rows.append(
                {
                    "time": normalize_timestamp(bar.date, contract_details.timeZoneId),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "wap": getattr(bar, "average", None),
                    "barCount": getattr(bar, "barCount", None),
                    "conId": contract_details.contract.conId,
                }
            )
        df = pd.DataFrame(rows)
        if not df.empty:
            df["dataset_id"] = dataset
        return df

    def _merge_append(self, output_path: Path, new_data: pd.DataFrame, dataset: str) -> pd.DataFrame:
        if output_path.exists():
            existing = pd.read_csv(output_path, parse_dates=["time"])
        else:
            existing = pd.DataFrame()
        if not existing.empty and "dataset_id" not in existing.columns:
            existing["dataset_id"] = dataset
        if not new_data.empty and "dataset_id" not in new_data.columns:
            new_data["dataset_id"] = dataset
        if new_data.empty and existing.empty:
            return new_data
        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=["time", "conId", "dataset_id"], keep="last")
        combined = combined.sort_values("time").reset_index(drop=True)
        return combined

    def _write_csv(self, output_path: Path, df: pd.DataFrame) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(output_path, df.to_csv(index=False))

    def _parse_date(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return pd.Timestamp(value, tz=timezone.utc).to_pydatetime()

    def _existing_last_timestamp(self, output_path: Path) -> datetime | None:
        if not output_path.exists():
            return None
        df = pd.read_csv(output_path, parse_dates=["time"])
        if df.empty:
            return None
        return pd.to_datetime(df["time"]).max().to_pydatetime()

    def _bar_delta(self, bar_size: str) -> timedelta:
        amount, unit = bar_size.split(maxsplit=1)
        value = int(amount)
        unit = unit.lower()
        if unit.startswith("sec"):
            return timedelta(seconds=value)
        if unit.startswith("min"):
            return timedelta(minutes=value)
        if unit.startswith("hour"):
            return timedelta(hours=value)
        if unit.startswith("day"):
            return timedelta(days=value)
        if unit.startswith("week"):
            return timedelta(weeks=value)
        return timedelta(days=value)