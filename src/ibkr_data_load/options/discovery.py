from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ib_insync import Contract, IB

from ibkr_data_load.config import AppConfig
from ibkr_data_load.utils import atomic_write


@dataclass(frozen=True)
class OptionChainEntry:
    trading_class: str
    exchange: str
    expirations: list[str]
    strikes: list[float]
    multiplier: str | None

    def to_dict(self) -> dict:
        return {
            "trading_class": self.trading_class,
            "exchange": self.exchange,
            "expirations": self.expirations,
            "strikes": self.strikes,
            "multiplier": self.multiplier,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "OptionChainEntry":
        return cls(
            trading_class=payload.get("trading_class", ""),
            exchange=payload.get("exchange", ""),
            expirations=list(payload.get("expirations", [])),
            strikes=[float(value) for value in payload.get("strikes", [])],
            multiplier=payload.get("multiplier"),
        )


@dataclass(frozen=True)
class OptionChainMetadata:
    underlying_conId: int
    symbol: str
    secType: str
    currency: str | None
    asof_utc: str
    entries: list[OptionChainEntry]

    def to_dict(self) -> dict:
        return {
            "underlying_conId": self.underlying_conId,
            "symbol": self.symbol,
            "secType": self.secType,
            "currency": self.currency,
            "asof_utc": self.asof_utc,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "OptionChainMetadata":
        return cls(
            underlying_conId=int(payload.get("underlying_conId", 0)),
            symbol=payload.get("symbol", ""),
            secType=payload.get("secType", ""),
            currency=payload.get("currency"),
            asof_utc=payload.get("asof_utc", ""),
            entries=[OptionChainEntry.from_dict(item) for item in payload.get("entries", [])],
        )

    def all_expirations(self) -> list[str]:
        expirations = {exp for entry in self.entries for exp in entry.expirations}
        return sorted(expirations)

    def all_strikes(self) -> list[float]:
        strikes = {strike for entry in self.entries for strike in entry.strikes}
        return sorted(strikes)

    def trading_classes(self) -> list[str]:
        classes = {entry.trading_class for entry in self.entries if entry.trading_class}
        return sorted(classes)

    def exchanges(self) -> list[str]:
        exchanges = {entry.exchange for entry in self.entries if entry.exchange}
        return sorted(exchanges)


def option_chain_metadata_path(output_dir: str | Path, instrument_id: str) -> Path:
    return (
        Path(output_dir)
        / instrument_id
        / "derivatives"
        / "options"
        / "meta"
        / "option_chain_discovery.json"
    )


def load_option_chain_metadata(path: Path) -> OptionChainMetadata:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return OptionChainMetadata.from_dict(payload)


def is_metadata_fresh(path: Path, max_age_hours: int) -> bool:
    if not path.exists():
        return False
    age = datetime.now(tz=timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return age.total_seconds() <= max_age_hours * 3600


def _dedupe_entries(entries: Iterable[OptionChainEntry]) -> list[OptionChainEntry]:
    merged: dict[tuple[str, str, str | None], OptionChainEntry] = {}
    for entry in entries:
        key = (entry.trading_class, entry.exchange, entry.multiplier)
        if key not in merged:
            merged[key] = OptionChainEntry(
                trading_class=entry.trading_class,
                exchange=entry.exchange,
                expirations=sorted(set(entry.expirations)),
                strikes=sorted(set(entry.strikes)),
                multiplier=entry.multiplier,
            )
            continue
        current = merged[key]
        merged[key] = OptionChainEntry(
            trading_class=current.trading_class,
            exchange=current.exchange,
            expirations=sorted(set(current.expirations).union(entry.expirations)),
            strikes=sorted(set(current.strikes).union(entry.strikes)),
            multiplier=current.multiplier,
        )
    return list(merged.values())


def discover_option_chain(
    ib: IB,
    underlying_contract: Contract,
    instrument_id: str,
    logger,
    config: AppConfig,
) -> OptionChainMetadata:
    params = ib.reqSecDefOptParams(
        underlying_contract.symbol,
        "",
        underlying_contract.secType,
        underlying_contract.conId,
    )
    entries: list[OptionChainEntry] = []
    for chain in params:
        expirations = sorted(chain.expirations or [])
        strikes = sorted(float(strike) for strike in chain.strikes or [])
        entries.append(
            OptionChainEntry(
                trading_class=chain.tradingClass or "",
                exchange=chain.exchange or "",
                expirations=expirations,
                strikes=strikes,
                multiplier=str(chain.multiplier) if chain.multiplier is not None else None,
            )
        )

    consolidated = _dedupe_entries(entries)
    if logger:
        logger.info(
            "option chain discovery returned %s entries (deduped to %s)",
            len(entries),
            len(consolidated),
        )

    metadata = OptionChainMetadata(
        underlying_conId=int(underlying_contract.conId or 0),
        symbol=underlying_contract.symbol,
        secType=underlying_contract.secType,
        currency=underlying_contract.currency,
        asof_utc=datetime.now(tz=timezone.utc).isoformat(),
        entries=consolidated,
    )
    path = option_chain_metadata_path(config.output_dir, instrument_id)
    atomic_write(path, json.dumps(metadata.to_dict(), indent=2))
    if logger:
        logger.info("option chain metadata saved path=%s", path)
    return metadata