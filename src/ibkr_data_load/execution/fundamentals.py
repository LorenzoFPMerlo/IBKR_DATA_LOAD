from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
from ib_insync import Contract, IB

from ibkr_data_load.config import AppConfig
from ibkr_data_load.datasets.registry import DatasetDefinition
from ibkr_data_load.dqc import run_fundamentals_dqc, write_fundamentals_dqc
from ibkr_data_load.utils import atomic_write, dataset_id, normalize_parameters

SCHEMA_VERSION = "1"
DATA_SOURCE = "ibkr"


@dataclass(frozen=True)
class FundamentalsResult:
    dataset_id: str
    output_path: Path
    row_count: int


def fetch_fundamentals(
    ib: IB,
    contract: Contract,
    dataset_def: DatasetDefinition,
    config: AppConfig,
    logger,
    run_id: str,
    instrument_id: str,
    report_type_override: str | None = None,
) -> FundamentalsResult:
    report_type = (
        report_type_override
        or config.fundamentals.default_report_type
        or dataset_def.default_parameters.get("reportType")
    )
    params = normalize_parameters({"reportType": report_type})
    dataset = dataset_id(
        con_id=str(contract.conId),
        dataset_name=dataset_def.name,
        parameters=params,
        data_source=DATA_SOURCE,
        timezone="UTC",
        schema_version=SCHEMA_VERSION,
    )
    output_path = (
        Path(config.output_dir)
        / instrument_id
        / "fundamentals"
        / f"fundamentals_report__{dataset}.csv"
    )
    payload = ib.reqFundamentalData(contract, report_type)
    if not payload:
        if logger:
            logger.warning(
                "run_id=%s fundamentals report unavailable (entitlement or no data) conId=%s report_type=%s",
                run_id,
                contract.conId,
                report_type,
            )
        _write_fundamentals_status(
            config.output_dir,
            instrument_id,
            report_type,
            run_id,
            reason="no entitlement or no data",
        )
        return FundamentalsResult(dataset_id=dataset, output_path=output_path, row_count=0)

    payload_type = "xml" if str(payload).lstrip().startswith("<") else "text"
    as_of = datetime.now(tz=timezone.utc).isoformat()
    row = {
        "conId": contract.conId,
        "symbol": contract.symbol,
        "secType": contract.secType,
        "currency": contract.currency,
        "reportType": report_type,
        "asOf_datetime_utc": as_of,
        "raw_payload_type": payload_type,
        "raw_payload": payload,
        "data_source": DATA_SOURCE,
        "dataset_id": dataset,
        "run_id": run_id,
    }
    df = pd.DataFrame([row])
    merged = _merge_append(output_path, df)
    _write_csv(output_path, merged)

    dqc_result = run_fundamentals_dqc(
        merged,
        required_columns=list(row.keys()),
        key_columns=["conId", "reportType", "asOf_datetime_utc"],
    )
    write_fundamentals_dqc(
        dqc_result,
        output_dir=Path(config.output_dir)
        / instrument_id
        / "fundamentals"
        / "meta"
        / "dqc"
        / dataset,
    )

    return FundamentalsResult(dataset_id=dataset, output_path=output_path, row_count=len(merged))


def _merge_append(output_path: Path, new_data: pd.DataFrame) -> pd.DataFrame:
    if output_path.exists():
        existing = pd.read_csv(output_path)
    else:
        existing = pd.DataFrame()
    if new_data.empty and existing.empty:
        return new_data
    combined = pd.concat([existing, new_data], ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["conId", "reportType", "asOf_datetime_utc"], keep="last"
    )
    combined = combined.sort_values("asOf_datetime_utc").reset_index(drop=True)
    return combined


def _write_csv(output_path: Path, df: pd.DataFrame) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output_path, df.to_csv(index=False))


def _write_fundamentals_status(
    output_dir: str,
    instrument_id: str,
    report_type: str,
    run_id: str,
    reason: str,
) -> None:
    payload = {
        "reportType": report_type,
        "run_id": run_id,
        "reason": reason,
        "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
    }
    status_path = (
        Path(output_dir)
        / instrument_id
        / "fundamentals"
        / "meta"
        / "fundamentals_report_status.json"
    )
    atomic_write(status_path, json.dumps(payload, indent=2))