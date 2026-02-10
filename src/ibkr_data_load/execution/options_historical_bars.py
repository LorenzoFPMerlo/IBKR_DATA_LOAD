from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from ib_insync import ContractDetails, IB, Option

from ibkr_data_load.checkpoint import Checkpoint, load_checkpoint, save_checkpoint
from ibkr_data_load.config import AppConfig
from ibkr_data_load.options.discovery import OptionChainMetadata
from ibkr_data_load.retry_backoff import build_retryer
from ibkr_data_load.utils import atomic_write, dataset_id, normalize_parameters, normalize_timestamp

SCHEMA_VERSION = "1"
DATA_SOURCE = "ibkr"


@dataclass(frozen=True)
class OptionHistoricalSelection:
    expirations_next: int | None = None
    expirations: list[str] | None = None
    strikes: list[float] | None = None
    strikes_nearest: int | None = None
    rights: str = "both"
    max_contracts: int | None = None
    bar_size: str | None = None
    duration: str | None = None


@dataclass(frozen=True)
class OptionHistoricalBarsResult:
    dataset_id: str
    output_root: Path
    row_count: int
    contracts_attempted: int
    contracts_succeeded: int
    contracts_failed: int
    chunks_attempted: int
    chunks_succeeded: int
    chunks_failed: int
    checkpoint_used: bool
    parameters_used: dict


def _norm_exp(value: str) -> str:
    cleaned = value.replace("-", "")
    return cleaned if len(cleaned) == 8 else value


def _select_expirations(metadata: OptionChainMetadata, selection: OptionHistoricalSelection) -> list[str]:
    all_exp = sorted({_norm_exp(x) for x in metadata.all_expirations()})
    explicit = sorted({_norm_exp(x) for x in (selection.expirations or [])})
    if explicit:
        return [x for x in explicit if x in all_exp]
    if selection.expirations_next and selection.expirations_next > 0:
        return all_exp[: selection.expirations_next]
    return all_exp[:1]


def _select_strikes(metadata: OptionChainMetadata, selection: OptionHistoricalSelection, reference_price: float | None) -> list[float]:
    all_strikes = sorted(set(metadata.all_strikes()))
    if selection.strikes:
        explicit = sorted(set(float(x) for x in selection.strikes))
        return [x for x in explicit if x in all_strikes]
    if selection.strikes_nearest and selection.strikes_nearest > 0 and all_strikes and reference_price:
        ordered = sorted(all_strikes, key=lambda x: abs(x - reference_price))
        return sorted(ordered[: selection.strikes_nearest])
    if selection.strikes_nearest and selection.strikes_nearest > 0 and all_strikes:
        mid = len(all_strikes) // 2
        half = max(1, selection.strikes_nearest)
        return all_strikes[max(0, mid - half) : mid + half]
    return all_strikes[:20]


def _fetch_underlying_price(ib: IB, contract, wait_seconds: float) -> float | None:
    ticker = ib.reqMktData(contract, "", snapshot=True)
    ib.sleep(wait_seconds)
    price = ticker.last or ticker.close or ticker.marketPrice()
    ib.cancelMktData(contract)
    return price


def _build_contracts(metadata: OptionChainMetadata, selection: OptionHistoricalSelection, reference_price: float | None) -> list[Option]:
    expirations = _select_expirations(metadata, selection)
    strikes = _select_strikes(metadata, selection, reference_price)
    rights = ["C", "P"] if selection.rights == "both" else [selection.rights]
    contracts: list[Option] = []
    for entry in metadata.entries:
        entry_exps = [exp for exp in expirations if _norm_exp(exp) in {_norm_exp(y) for y in entry.expirations}]
        entry_strikes = [strike for strike in strikes if strike in entry.strikes]
        for exp in entry_exps:
            for strike in entry_strikes:
                for right in rights:
                    contracts.append(
                        Option(
                            metadata.symbol,
                            _norm_exp(exp),
                            float(strike),
                            right,
                            entry.exchange or "SMART",
                            currency=metadata.currency or "USD",
                            tradingClass=entry.trading_class or metadata.symbol,
                            multiplier=entry.multiplier or "",
                        )
                    )
    deduped: dict[tuple, Option] = {}
    for c in contracts:
        deduped[(c.lastTradeDateOrContractMonth, c.strike, c.right, c.exchange, c.tradingClass)] = c
    values = list(deduped.values())
    if selection.max_contracts and selection.max_contracts > 0:
        return values[: selection.max_contracts]
    return values


def _bar_delta(bar_size: str) -> timedelta:
    amount, unit = bar_size.split(maxsplit=1)
    value = int(amount)
    unit = unit.lower()
    if unit.startswith("sec"):
        return timedelta(seconds=value)
    if unit.startswith("min"):
        return timedelta(minutes=value)
    if unit.startswith("hour"):
        return timedelta(hours=value)
    return timedelta(days=value)


def _bars_to_df(bars, contract, dataset: str) -> pd.DataFrame:
    rows = []
    for bar in bars:
        rows.append(
            {
                "time": normalize_timestamp(bar.date, "UTC"),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "wap": getattr(bar, "average", None),
                "barCount": getattr(bar, "barCount", None),
                "option_conId": contract.conId,
                "dataset_id": dataset,
            }
        )
    return pd.DataFrame(rows)


def run_option_contract_historical_bars(
    ib: IB,
    instrument_id: str,
    underlying_contract_details: ContractDetails,
    metadata: OptionChainMetadata,
    config: AppConfig,
    run_id: str,
    logger,
    selection: OptionHistoricalSelection,
    start_date: str | None,
    end_date: str | None,
    resume: bool,
) -> OptionHistoricalBarsResult:
    bar_size = selection.bar_size or config.historical_bars.bar_size
    duration = selection.duration or config.historical_bars.chunk_duration
    params = normalize_parameters(
        {
            "barSize": bar_size,
            "duration": duration,
            "whatToShow": config.historical_bars.what_to_show,
            "useRTH": config.historical_bars.use_rth,
            "rights": selection.rights,
            "expirations_next": selection.expirations_next,
            "strikes_nearest": selection.strikes_nearest,
            "max_contracts": selection.max_contracts,
        }
    )
    dataset = dataset_id(
        con_id=str(underlying_contract_details.contract.conId),
        dataset_name="option_contract_historical_bars",
        parameters=params,
        data_source=DATA_SOURCE,
        timezone="UTC",
        schema_version=SCHEMA_VERSION,
    )
    out_root = Path(config.output_dir) / instrument_id / "derivatives" / "options" / "contracts"
    checkpoint_path = Path(config.output_dir) / instrument_id / "meta" / "checkpoints" / f"{dataset}.json"
    availability_root = Path(config.output_dir) / instrument_id / "derivatives" / "options" / "meta" / "availability"

    start_dt = pd.Timestamp(start_date, tz=timezone.utc).to_pydatetime() if start_date else None
    end_dt = pd.Timestamp(end_date, tz=timezone.utc).to_pydatetime() if end_date else datetime.now(tz=timezone.utc)
    checkpoint_used = False
    contract_start_index = 0
    if resume:
        checkpoint = load_checkpoint(checkpoint_path)
        if checkpoint:
            checkpoint_used = True
            contract_start_index = int(checkpoint.payload.get("contract_index", 0))

    reference_price = _fetch_underlying_price(ib, underlying_contract_details.contract, config.options_snapshot.snapshot_wait_seconds)
    contracts = _build_contracts(metadata, selection, reference_price)
    contracts_attempted = contracts_succeeded = contracts_failed = 0
    chunks_attempted = chunks_succeeded = chunks_failed = 0
    total_rows = 0

    retryer = build_retryer(
        config.pacing,
        logger,
        run_id,
        context={"dataset": "option_contract_historical_bars", "instrument_id": instrument_id},
    )

    for contract_index, option_contract in enumerate(contracts):
        if contract_index < contract_start_index:
            continue
        contracts_attempted += 1
        qualified = ib.qualifyContracts(option_contract)
        if not qualified:
            contracts_failed += 1
            continue
        option = qualified[0]
        current_end = end_dt
        per_contract_frames: list[pd.DataFrame] = []
        had_data = False
        failed_contract = False
        while start_dt is None or current_end > start_dt:
            chunks_attempted += 1
            try:
                bars = None
                for attempt in retryer:
                    with attempt:
                        bars = ib.reqHistoricalData(
                            option,
                            endDateTime=current_end,
                            durationStr=duration,
                            barSizeSetting=bar_size,
                            whatToShow=config.historical_bars.what_to_show,
                            useRTH=config.historical_bars.use_rth,
                            formatDate=config.historical_bars.format_date,
                        )
                        break
                if not bars:
                    break
                df = _bars_to_df(bars, option, dataset)
                if start_dt is not None:
                    df = df[df["time"] >= pd.Timestamp(start_dt)]
                if df.empty:
                    break
                had_data = True
                per_contract_frames.append(df)
                chunks_succeeded += 1
                current_end = df["time"].min().to_pydatetime() - _bar_delta(bar_size)
            except Exception as exc:
                chunks_failed += 1
                failed_contract = True
                availability_payload = {
                    "option_conId": option.conId,
                    "expiration": option.lastTradeDateOrContractMonth,
                    "strike": option.strike,
                    "right": option.right,
                    "reason": str(exc),
                    "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
                }
                availability_path = availability_root / f"{option.conId}.json"
                atomic_write(availability_path, json.dumps(availability_payload, indent=2))
                if logger:
                    logger.warning("option historical unavailable for conId=%s reason=%s", option.conId, exc)
                break

        if had_data and per_contract_frames:
            merged = pd.concat(per_contract_frames, ignore_index=True)
            merged = merged.drop_duplicates(subset=["time", "option_conId", "dataset_id"], keep="last")
            merged = merged.sort_values("time").reset_index(drop=True)
            out_path = (
                out_root
                / option.lastTradeDateOrContractMonth
                / option.right
                / str(option.strike)
                / str(option.conId)
                / f"option_contract_historical_bars__{dataset}.csv"
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(out_path, merged.to_csv(index=False))
            total_rows += len(merged)
            contracts_succeeded += 1
        elif failed_contract or not had_data:
            contracts_failed += 1
            if not had_data:
                availability_payload = {
                    "option_conId": option.conId,
                    "expiration": option.lastTradeDateOrContractMonth,
                    "strike": option.strike,
                    "right": option.right,
                    "reason": "empty_response_or_unavailable",
                    "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
                }
                availability_path = availability_root / f"{option.conId}.json"
                atomic_write(availability_path, json.dumps(availability_payload, indent=2))

        save_checkpoint(
            checkpoint_path,
            Checkpoint(
                dataset_id=dataset,
                watermark=str(contract_index + 1),
                payload={"contract_index": contract_index + 1},
            ),
        )
        time.sleep(config.historical_bars.min_request_interval_seconds)

    return OptionHistoricalBarsResult(
        dataset_id=dataset,
        output_root=out_root,
        row_count=total_rows,
        contracts_attempted=contracts_attempted,
        contracts_succeeded=contracts_succeeded,
        contracts_failed=contracts_failed,
        chunks_attempted=chunks_attempted,
        chunks_succeeded=chunks_succeeded,
        chunks_failed=chunks_failed,
        checkpoint_used=checkpoint_used,
        parameters_used=params,
    )
