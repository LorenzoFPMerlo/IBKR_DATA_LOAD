from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
from ib_insync import ContractDetails, IB, Option, Ticker

from ibkr_data_load.config import AppConfig, OptionsSnapshotConfig
from ibkr_data_load.options.discovery import OptionChainMetadata
from ibkr_data_load.utils import atomic_write, dataset_id, normalize_parameters

SCHEMA_VERSION = "1"
DATA_SOURCE = "ibkr"


@dataclass(frozen=True)
class OptionSurfaceSnapshotResult:
    dataset_id: str
    output_path: Path
    row_count: int


def filter_expirations(expirations: Iterable[str], max_expirations: int) -> list[str]:
    sorted_exps = sorted(expirations)
    if max_expirations <= 0:
        return sorted_exps
    return sorted_exps[:max_expirations]


def filter_strikes(
    strikes: Iterable[float],
    underlying_price: float | None,
    strike_band_pct: float,
    strike_step: int,
) -> list[float]:
    sorted_strikes = sorted(set(float(value) for value in strikes))
    if underlying_price and strike_band_pct > 0:
        lower = underlying_price * (1 - strike_band_pct)
        upper = underlying_price * (1 + strike_band_pct)
        sorted_strikes = [strike for strike in sorted_strikes if lower <= strike <= upper]
    if strike_step > 1:
        sorted_strikes = sorted_strikes[::strike_step]
    return sorted_strikes


def build_snapshot_plan(
    metadata: OptionChainMetadata,
    config: OptionsSnapshotConfig,
    underlying_price: float | None = None,
) -> dict:
    expirations = filter_expirations(metadata.all_expirations(), config.max_expirations)
    strikes = filter_strikes(
        metadata.all_strikes(),
        underlying_price=underlying_price,
        strike_band_pct=config.strike_band_pct,
        strike_step=config.strike_step,
    )
    estimated_contracts = 0
    for entry in metadata.entries:
        entry_expirations = [exp for exp in expirations if exp in entry.expirations]
        entry_strikes = [strike for strike in strikes if strike in entry.strikes]
        estimated_contracts += len(entry_expirations) * len(entry_strikes) * 2
    batches = math.ceil(estimated_contracts / config.max_contracts_per_batch) if estimated_contracts else 0
    return {
        "expiration_count": len(expirations),
        "strike_count": len(strikes),
        "estimated_contracts": estimated_contracts,
        "estimated_batches": batches,
        "expirations": expirations,
        "strikes": strikes,
        "underlying_price": underlying_price,
    }


def _fetch_underlying_price(ib: IB, contract, logger, wait_seconds: float) -> float | None:
    ticker = ib.reqMktData(contract, "", snapshot=True)
    ib.sleep(wait_seconds)
    price = ticker.last or ticker.close or ticker.marketPrice()
    if price is None and logger:
        logger.warning("Unable to determine underlying price for strike filtering")
    ib.cancelMktData(contract)
    return price


def _batch_contracts(contracts: list[Option], batch_size: int) -> Iterable[list[Option]]:
    for start in range(0, len(contracts), batch_size):
        yield contracts[start : start + batch_size]


def _build_contracts(
    metadata: OptionChainMetadata,
    expirations: list[str],
    strikes: list[float],
    exchange_override: str,
) -> list[Option]:
    contracts: list[Option] = []
    for entry in metadata.entries:
        entry_expirations = [exp for exp in expirations if exp in entry.expirations]
        entry_strikes = [strike for strike in strikes if strike in entry.strikes]
        exchange = exchange_override or entry.exchange or "SMART"
        for expiration in entry_expirations:
            for strike in entry_strikes:
                for right in ("C", "P"):
                    contracts.append(
                        Option(
                            metadata.symbol,
                            expiration,
                            strike,
                            right,
                            exchange,
                            currency=metadata.currency or "USD",
                            tradingClass=entry.trading_class or metadata.symbol,
                            multiplier=entry.multiplier or "",
                        )
                    )
    return contracts


def _ticker_to_row(
    ticker: Ticker,
    snapshot_time: datetime,
    underlying_con_id: int,
    dataset: str,
    run_id: str,
) -> dict:
    contract = ticker.contract
    greeks = ticker.modelGreeks or ticker.optionGreeks
    return {
        "snapshot_datetime_utc": snapshot_time.isoformat(),
        "underlying_conId": underlying_con_id,
        "option_conId": contract.conId,
        "expiration": contract.lastTradeDateOrContractMonth,
        "strike": contract.strike,
        "right": contract.right,
        "multiplier": contract.multiplier,
        "bid": ticker.bid,
        "ask": ticker.ask,
        "last": ticker.last,
        "bidSize": ticker.bidSize,
        "askSize": ticker.askSize,
        "lastSize": ticker.lastSize,
        "volume": getattr(ticker, "volume", None),
        "openInterest": getattr(ticker, "openInterest", None),
        "impliedVolatility": getattr(greeks, "impliedVol", None) if greeks else None,
        "delta": getattr(greeks, "delta", None) if greeks else None,
        "gamma": getattr(greeks, "gamma", None) if greeks else None,
        "vega": getattr(greeks, "vega", None) if greeks else None,
        "theta": getattr(greeks, "theta", None) if greeks else None,
        "pvDividend": getattr(greeks, "pvDividend", None) if greeks else None,
        "undPrice": getattr(greeks, "undPrice", None) if greeks else None,
        "tradingClass": contract.tradingClass,
        "exchange": contract.exchange,
        "currency": contract.currency,
        "data_source": DATA_SOURCE,
        "dataset_id": dataset,
        "run_id": run_id,
    }


def _parse_expiration_date(expiration: str) -> datetime | None:
    if not expiration:
        return None
    try:
        return datetime.strptime(expiration, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def update_contract_registry(
    existing: pd.DataFrame,
    contracts: Iterable[Option],
    underlying_con_id: int,
    snapshot_time: datetime,
) -> pd.DataFrame:
    rows = []
    for contract in contracts:
        rows.append(
            {
                "underlying_conId": underlying_con_id,
                "option_conId": contract.conId,
                "expiration": contract.lastTradeDateOrContractMonth,
                "strike": contract.strike,
                "right": contract.right,
                "tradingClass": contract.tradingClass,
                "exchange": contract.exchange,
                "currency": contract.currency,
                "multiplier": contract.multiplier,
                "status": "active",
                "first_seen": snapshot_time.isoformat(),
                "last_seen": snapshot_time.isoformat(),
            }
        )
    new_df = pd.DataFrame(rows)
    if existing.empty:
        merged = new_df
    else:
        merged = pd.concat([existing, new_df], ignore_index=True)
        merged = (
            merged.sort_values("last_seen")
            .drop_duplicates(
                subset=[
                    "underlying_conId",
                    "option_conId",
                    "expiration",
                    "strike",
                    "right",
                ],
                keep="last",
            )
            .reset_index(drop=True)
        )
        if "first_seen" in existing.columns:
            merged = merged.merge(
                existing[
                    [
                        "underlying_conId",
                        "option_conId",
                        "expiration",
                        "strike",
                        "right",
                        "first_seen",
                    ]
                ],
                on=["underlying_conId", "option_conId", "expiration", "strike", "right"],
                how="left",
                suffixes=("", "_orig"),
            )
            merged["first_seen"] = merged["first_seen_orig"].fillna(merged["first_seen"])
            merged = merged.drop(columns=["first_seen_orig"])
    today = snapshot_time.date()
    if not merged.empty:
        statuses = []
        for expiration in merged["expiration"].astype(str):
            exp_dt = _parse_expiration_date(expiration)
            if exp_dt and exp_dt.date() < today:
                statuses.append("expired")
            else:
                statuses.append("active")
        merged["status"] = statuses
    return merged


def run_option_surface_snapshot(
    ib: IB,
    instrument_id: str,
    contract_details: ContractDetails,
    metadata: OptionChainMetadata,
    config: AppConfig,
    run_id: str,
    logger,
) -> OptionSurfaceSnapshotResult:
    snapshot_cfg = config.options_snapshot
    if not snapshot_cfg.enabled:
        return OptionSurfaceSnapshotResult(dataset_id="", output_path=Path(""), row_count=0)

    underlying_contract = contract_details.contract
    underlying_price = _fetch_underlying_price(
        ib, underlying_contract, logger, snapshot_cfg.snapshot_wait_seconds
    )
    plan = build_snapshot_plan(metadata, snapshot_cfg, underlying_price=underlying_price)
    expirations = plan["expirations"]
    strikes = plan["strikes"]

    params = normalize_parameters(
        {
            "snapshotDate": datetime.now(tz=timezone.utc).date().isoformat(),
            "max_contracts_per_batch": snapshot_cfg.max_contracts_per_batch,
            "max_expirations": snapshot_cfg.max_expirations,
            "strike_band_pct": snapshot_cfg.strike_band_pct,
            "strike_step": snapshot_cfg.strike_step,
        }
    )
    dataset = dataset_id(
        con_id=str(underlying_contract.conId),
        dataset_name="option_surface_snapshot_daily",
        parameters=params,
        data_source=DATA_SOURCE,
        timezone="UTC",
        schema_version=SCHEMA_VERSION,
    )

    snapshot_date = datetime.now(tz=timezone.utc)
    output_path = (
        Path(config.output_dir)
        / instrument_id
        / "derivatives"
        / "options"
        / "snapshots"
        / snapshot_date.strftime("%Y/%m/%d")
        / f"option_surface_snapshot_daily__{dataset}.csv"
    )
    registry_path = (
        Path(config.output_dir)
        / instrument_id
        / "derivatives"
        / "options"
        / "meta"
        / "contract_registry.csv"
    )

    contracts = _build_contracts(metadata, expirations, strikes, snapshot_cfg.exchange)
    rows: list[dict] = []
    qualified_contracts: list[Option] = []

    for batch in _batch_contracts(contracts, snapshot_cfg.max_contracts_per_batch):
        retries = 0
        while True:
            try:
                qualified_batch = ib.qualifyContracts(*batch)
                qualified_contracts.extend(qualified_batch)
                tickers = [
                    ib.reqMktData(contract, "", snapshot=True) for contract in qualified_batch
                ]
                ib.sleep(snapshot_cfg.snapshot_wait_seconds)
                snapshot_time = datetime.now(tz=timezone.utc)
                for ticker in tickers:
                    rows.append(
                        _ticker_to_row(
                            ticker,
                            snapshot_time,
                            underlying_contract.conId,
                            dataset,
                            run_id,
                        )
                    )
                    ib.cancelMktData(ticker.contract)
                break
            except Exception as exc:
                retries += 1
                if retries > 3:
                    raise
                if logger:
                    logger.warning(
                        "run_id=%s option snapshot batch retry=%s error=%s",
                        run_id,
                        retries,
                        exc,
                    )
                time.sleep(min(2**retries, 30))
        time.sleep(snapshot_cfg.sleep_between_batches_seconds)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["dataset_id"] = dataset
    merged = _merge_snapshot(output_path, df)
    _write_snapshot(output_path, merged)

    registry_existing = (
        pd.read_csv(registry_path)
        if registry_path.exists()
        else pd.DataFrame()
    )
    registry_updated = update_contract_registry(
        registry_existing,
        qualified_contracts,
        underlying_contract.conId,
        snapshot_date,
    )
    if not registry_updated.empty:
        atomic_write(registry_path, registry_updated.to_csv(index=False))

    return OptionSurfaceSnapshotResult(
        dataset_id=dataset,
        output_path=output_path,
        row_count=len(merged),
    )


def _merge_snapshot(output_path: Path, new_data: pd.DataFrame) -> pd.DataFrame:
    """Merge snapshot data with idempotent deduplication on primary keys."""
    if output_path.exists():
        existing = pd.read_csv(output_path)
    else:
        existing = pd.DataFrame()
    if new_data.empty and existing.empty:
        return new_data
    combined = pd.concat([existing, new_data], ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["snapshot_datetime_utc", "option_conId", "right", "strike", "expiration"],
        keep="last",
    )
    return combined


def _write_snapshot(output_path: Path, df: pd.DataFrame) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output_path, df.to_csv(index=False))