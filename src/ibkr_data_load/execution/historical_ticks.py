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
from ibkr_data_load.dqc import run_ticks_dqc, write_ticks_dqc
from ibkr_data_load.retry_backoff import build_retryer
from ibkr_data_load.utils import atomic_write, dataset_id, normalize_parameters

SCHEMA_VERSION = "1"
DATA_SOURCE = "ibkr"


@dataclass(frozen=True)
class HistoricalTicksResult:
    dataset_id: str
    output_path: Path
    row_count: int


class HistoricalTicksExecutor:
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
        dataset_name: str,
        what_to_show: str,
        start_date: str | None = None,
        end_date: str | None = None,
        resume: bool = False,
    ) -> HistoricalTicksResult:
        settings = self.config.historical_ticks
        if not settings.enabled:
            return HistoricalTicksResult(dataset_id="", output_path=Path(""), row_count=0)

        params = normalize_parameters({"whatToShow": what_to_show})
        dataset = dataset_id(
            con_id=str(contract_details.contract.conId),
            dataset_name=dataset_name,
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
            / f"{dataset_name}__{dataset}.csv"
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
        if start_dt is None:
            start_dt = end_dt - timedelta(seconds=settings.chunk_seconds)
        if resume:
            checkpoint = load_checkpoint(checkpoint_path)
            if checkpoint:
                start_dt = datetime.fromisoformat(checkpoint.watermark)
                if settings.resume_overlap_seconds > 0:
                    start_dt = start_dt - timedelta(seconds=settings.resume_overlap_seconds)

        ticks = []
        try:
            current_start = start_dt
            while current_start < end_dt:
                current_end = min(current_start + timedelta(seconds=settings.chunk_seconds), end_dt)
                chunk = self._fetch_chunk(
                    ib,
                    contract_details,
                    current_start,
                    current_end,
                    what_to_show,
                    settings,
                )
                if not chunk:
                    current_start = current_end
                    continue
                ticks.extend(chunk)
                last_time = max(t.time for t in chunk)
                if isinstance(last_time, datetime):
                    last_dt = (
                        last_time.replace(tzinfo=timezone.utc)
                        if last_time.tzinfo is None
                        else last_time
                    )
                else:
                    last_dt = datetime.fromtimestamp(last_time, tz=timezone.utc)
                current_start = max(current_end, last_dt + timedelta(seconds=1))
                save_checkpoint(
                    checkpoint_path,
                    Checkpoint(
                        dataset_id=dataset,
                        watermark=last_dt.isoformat(),
                        payload={"last_end": current_end.isoformat()},
                    ),
                )
        except Exception as exc:
            if self.logger:
                self.logger.warning(
                    "run_id=%s historical ticks skipped due to entitlement/availability error=%s",
                    self.run_id,
                    exc,
                )
            return HistoricalTicksResult(
                dataset_id=dataset, output_path=output_path, row_count=0
            )

        df = self._ticks_to_frame(ticks, contract_details, dataset, what_to_show)
        merged = self._merge_append(output_path, df, dataset)
        self._write_csv(output_path, merged)

        if what_to_show == "BID_ASK":
            key_columns = ["time", "conId", "bid", "ask", "bidSize", "askSize", "dataset_id"]
        else:
            key_columns = ["time", "conId", "price", "size", "dataset_id"]
        dqc_result = run_ticks_dqc(merged, time_column="time", key_columns=key_columns)
        write_ticks_dqc(
            dqc_result,
            output_dir=Path(self.config.output_dir)
            / instrument_id
            / "meta"
            / "dqc"
            / dataset,
        )

        return HistoricalTicksResult(dataset_id=dataset, output_path=output_path, row_count=len(merged))

    def _fetch_chunk(
        self,
        ib: IB,
        contract_details: ContractDetails,
        start_dt: datetime,
        end_dt: datetime,
        what_to_show: str,
        settings,
    ) -> Iterable:
        retryer = build_retryer(
            self.config.pacing,
            self.logger,
            self.run_id,
            context={
                "dataset": "historical_ticks",
                "conId": contract_details.contract.conId,
                "what_to_show": what_to_show,
            },
        )
        for attempt in retryer:
            with attempt:
                self._apply_pacing(settings.min_request_interval_seconds)
                ticks = ib.reqHistoricalTicks(
                    contract_details.contract,
                    startDateTime=start_dt,
                    endDateTime=end_dt,
                    numberOfTicks=settings.max_ticks_per_request,
                    whatToShow=what_to_show,
                    useRth=0,
                    ignoreSize=False,
                )
                self._last_request_at = time.time()
                return ticks

    def _apply_pacing(self, min_interval: float) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.time() - self._last_request_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    def _ticks_to_frame(
        self,
        ticks: Iterable,
        contract_details: ContractDetails,
        dataset: str,
        what_to_show: str,
    ) -> pd.DataFrame:
        rows = []
        for tick in ticks:
            if isinstance(tick.time, datetime):
                tick_time = tick.time.replace(tzinfo=timezone.utc) if tick.time.tzinfo is None else tick.time
            else:
                tick_time = datetime.fromtimestamp(tick.time, tz=timezone.utc)
            row = {
                "time": tick_time.isoformat(),
                "conId": contract_details.contract.conId,
                "dataset_id": dataset,
                "run_id": self.run_id,
            }
            if what_to_show == "BID_ASK":
                row.update(
                    {
                        "bid": tick.priceBid,
                        "ask": tick.priceAsk,
                        "bidSize": tick.sizeBid,
                        "askSize": tick.sizeAsk,
                    }
                )
            else:
                row.update(
                    {
                        "price": tick.price,
                        "size": tick.size,
                        "exchange": getattr(tick, "exchange", None),
                        "specialConditions": getattr(tick, "specialConditions", None),
                    }
                )
            rows.append(row)
        df = pd.DataFrame(rows)
        if not df.empty:
            df["dataset_id"] = dataset
        return df

    def _merge_append(self, output_path: Path, new_data: pd.DataFrame, dataset: str) -> pd.DataFrame:
        if output_path.exists():
            existing = pd.read_csv(output_path)
        else:
            existing = pd.DataFrame()
        if new_data.empty and existing.empty:
            return new_data
        if not existing.empty and "dataset_id" not in existing.columns:
            existing["dataset_id"] = dataset
        if not new_data.empty and "dataset_id" not in new_data.columns:
            new_data["dataset_id"] = dataset
        combined = pd.concat([existing, new_data], ignore_index=True)
        key_columns = ["time", "conId", "dataset_id"]
        if "price" in combined.columns:
            key_columns.extend(["price", "size"])
        elif "bid" in combined.columns:
            key_columns.extend(["bid", "ask", "bidSize", "askSize"])
        combined = combined.drop_duplicates(subset=key_columns, keep="last")
        combined = combined.sort_values("time").reset_index(drop=True)
        return combined

    def _write_csv(self, output_path: Path, df: pd.DataFrame) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(output_path, df.to_csv(index=False))

    def _parse_date(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return pd.Timestamp(value, tz=timezone.utc).to_pydatetime()