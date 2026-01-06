from __future__ import annotations

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
from ibkr_data_load.utils import (
    atomic_write,
    dataset_id,
    normalize_parameters,
    normalize_timestamp,
)

SCHEMA_VERSION = "1"
DATA_SOURCE = "ibkr"


@dataclass
class HistoricalBarsResult:
    dataset_id: str
    output_path: Path
    row_count: int


class HistoricalBarsExecutor:
    def __init__(self, config: AppConfig, run_id: str, logger=None) -> None:
        self.config = config
        self.run_id = run_id
        self.logger = logger
        self._last_request_at: float | None = None

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

        params = normalize_parameters(
            {
                "barSize": settings.bar_size,
                "whatToShow": settings.what_to_show,
                "useRTH": settings.use_rth,
            }
        )
        dataset = dataset_id(
            con_id=str(contract_details.contract.conId),
            dataset_name="historical_bars",
            parameters=params,
            data_source=DATA_SOURCE,
            timezone="UTC",
            schema_version=SCHEMA_VERSION,
        )

        output_path = (
            Path(self.config.output_dir)
            / instrument_id
            / "market"
            / "historical"
            / f"historical_bars__{dataset}.csv"
        )
        checkpoint_path = (
            Path(self.config.output_dir)
            / instrument_id
            / "meta"
            / "checkpoints"
            / f"{dataset}.json"
        )

        start_dt = self._parse_date(start_date or settings.start_date)
        end_dt = self._parse_date(end_date or settings.end_date) or datetime.now(tz=timezone.utc)

        existing_last = self._existing_last_timestamp(output_path)
        if existing_last and not resume:
            overlap = self._bar_delta(settings.bar_size) * settings.overlap_bars
            overlap_start = existing_last - overlap
            if start_dt is None or overlap_start > start_dt:
                start_dt = overlap_start

        if resume:
            checkpoint = load_checkpoint(checkpoint_path)
            if checkpoint:
                end_dt = datetime.fromisoformat(checkpoint.watermark)

        chunk_duration = settings.chunk_duration
        expected_delta = self._bar_delta(settings.bar_size)
        all_new = []

        current_end = end_dt
        while start_dt is None or current_end > start_dt:
            bars = self._fetch_chunk(
                ib,
                contract_details,
                current_end,
                chunk_duration,
                settings,
            )
            if not bars:
                break
            df = self._bars_to_frame(bars, contract_details, dataset)
            if start_dt is not None:
                df = df[df["time"] >= pd.Timestamp(start_dt)]
            if df.empty:
                break
            all_new.append(df)
            earliest = df["time"].min().to_pydatetime()
            save_checkpoint(
                checkpoint_path,
                Checkpoint(dataset_id=dataset, watermark=earliest.isoformat(), payload={}),
            )
            current_end = earliest - expected_delta

        if all_new:
            combined_new = pd.concat(all_new, ignore_index=True)
        else:
            combined_new = pd.DataFrame(
                columns=[
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "wap",
                    "barCount",
                    "conId",
                    "dataset_id",
                ]
            )

        merged = self._merge_append(output_path, combined_new, dataset)
        self._write_csv(output_path, merged)

        dqc_result = run_bars_dqc(
            merged,
            time_column="time",
            key_columns=["time", "conId", "dataset_id"],
            expected_delta=expected_delta,
        )
        write_bars_dqc(
            dqc_result,
            output_dir=Path(self.config.output_dir) / instrument_id / "meta" / "dqc" / dataset,
        )

        return HistoricalBarsResult(dataset_id=dataset, output_path=output_path, row_count=len(merged))

    def _fetch_chunk(
        self,
        ib: IB,
        contract_details: ContractDetails,
        end_dt: datetime,
        duration: str,
        settings,
    ) -> Iterable:
        self._apply_pacing(settings.min_request_interval_seconds)
        retries = 0
        while True:
            try:
                bars = ib.reqHistoricalData(
                    contract_details.contract,
                    endDateTime=end_dt,
                    durationStr=duration,
                    barSizeSetting=settings.bar_size,
                    whatToShow=settings.what_to_show,
                    useRTH=settings.use_rth,
                    formatDate=settings.format_date,
                )
                self._last_request_at = time.time()
                return bars
            except Exception as exc:
                retries += 1
                if retries > settings.max_retries:
                    raise
                backoff = min(2 ** retries, 60)
                if self.logger:
                    self.logger.warning(
                        "run_id=%s historical data retry=%s error=%s",
                        self.run_id,
                        retries,
                        exc,
                    )
                time.sleep(backoff)

    def _apply_pacing(self, min_interval: float) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.time() - self._last_request_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    def _bars_to_frame(
        self, bars: Iterable, contract_details: ContractDetails, dataset: str
    ) -> pd.DataFrame:
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

    def _merge_append(
        self, output_path: Path, new_data: pd.DataFrame, dataset: str
    ) -> pd.DataFrame:
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
        content = df.to_csv(index=False)
        atomic_write(output_path, content)

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