from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ib_insync import Bond, Contract, ContractDetails, IB, Index, Option, Stock

from ibkr_data_load.config import AppConfig
from ibkr_data_load.logging import build_logger
from ibkr_data_load.utils import atomic_write, normalize_filename


class InstrumentNotFoundError(RuntimeError):
    pass


class AmbiguousInstrumentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedInstrument:
    instrument_id: str
    contract_details: ContractDetails


SEC_TYPE_MAP = {
    "stock": "STK",
    "etf": "STK",
    "index": "IND",
    "future": "FUT",
    "option": "OPT",
    "bond": "BOND",
}


class InstrumentResolver:
    def __init__(self, config: AppConfig, run_id: str, logger=None) -> None:
        self.config = config
        self.run_id = run_id
        self.logger = logger or build_logger(config.logging)

    def resolve(self, ib: IB, identifier: str, identifier_type: str) -> ResolvedInstrument:
        if identifier_type not in {"isin", "ticker"}:
            raise ValueError("identifier_type must be 'isin' or 'ticker'")
        if identifier_type == "isin":
            candidates = self._resolve_isin(ib, identifier)
        else:
            candidates = self._resolve_ticker(ib, identifier)

        filtered = self._apply_hints(candidates)
        if not filtered:
            raise InstrumentNotFoundError(f"No contract found for {identifier}")
        if len(filtered) > 1:
            candidate_payload = [self._format_candidate(c) for c in filtered]
            raise AmbiguousInstrumentError(
                f"Ambiguous instrument for {identifier}: {json.dumps(candidate_payload, indent=2)}"
            )

        selected = filtered[0]
        instrument_id = self._canonical_id(selected.contract)
        self._persist_resolution(identifier, identifier_type, candidates, selected, instrument_id)
        return ResolvedInstrument(instrument_id=instrument_id, contract_details=selected)

    def _resolve_isin(self, ib: IB, isin: str) -> list[ContractDetails]:
        contract = Contract(secIdType="ISIN", secId=isin)
        try:
            details = ib.reqContractDetails(contract)
        except Exception as exc:
            self.logger.warning(
                "run_id=%s ISIN search failed, fallback needed: %s",
                self.run_id,
                exc,
            )
            details = []
        return list(details)

    def _resolve_ticker(self, ib: IB, ticker: str) -> list[ContractDetails]:
        candidates: list[ContractDetails] = []
        try:
            matches = ib.reqMatchingSymbols(ticker)
            for match in matches:
                contract = match.contract
                details = ib.reqContractDetails(contract)
                candidates.extend(details)
        except Exception as exc:
            self.logger.warning(
                "run_id=%s matching symbols failed, fallback to heuristics: %s",
                self.run_id,
                exc,
            )

        if candidates:
            return candidates

        for sec_type in self.config.instruments.security_types:
            contract = self._contract_for_type(ticker, sec_type)
            if contract is None:
                continue
            details = ib.reqContractDetails(contract)
            candidates.extend(details)
        return candidates

    def _contract_for_type(self, ticker: str, sec_type: str) -> Contract | None:
        exchange = self.config.instruments.preferred_exchange or "SMART"
        if sec_type == "stock" or sec_type == "etf":
            return Stock(ticker, exchange, self.config.instruments.currency or "USD")
        if sec_type == "index":
            return Index(ticker, exchange)
        if sec_type == "future":
            return Contract(symbol=ticker, secType="FUT", exchange=exchange)
        if sec_type == "option":
            return Option(ticker, "", 0, "", exchange)
        if sec_type == "bond":
            return Bond()
        return None

    def _apply_hints(self, candidates: Iterable[ContractDetails]) -> list[ContractDetails]:
        filtered = list(candidates)
        allowed = {SEC_TYPE_MAP.get(sec, sec).upper() for sec in self.config.instruments.security_types}
        filtered = [c for c in filtered if c.contract.secType.upper() in allowed]

        preferred_exchange = self.config.instruments.preferred_exchange
        if preferred_exchange:
            preferred_exchange = preferred_exchange.upper()
            filtered = [
                c
                for c in filtered
                if c.contract.exchange.upper() == preferred_exchange
                or (c.contract.primaryExchange or "").upper() == preferred_exchange
            ]

        currency = self.config.instruments.currency
        if currency:
            filtered = [c for c in filtered if c.contract.currency == currency]
        return filtered

    def _canonical_id(self, contract: Contract) -> str:
        symbol = normalize_filename(contract.symbol or "UNKNOWN")
        sec_type = normalize_filename(contract.secType or "UNKNOWN")
        currency = normalize_filename(contract.currency or "UNKNOWN")
        con_id = normalize_filename(str(contract.conId or "0"))
        return f"{symbol}_{sec_type}_{currency}_{con_id}"

    def _format_candidate(self, details: ContractDetails) -> dict[str, str]:
        contract = details.contract
        return {
            "symbol": contract.symbol,
            "secType": contract.secType,
            "exchange": contract.exchange,
            "currency": contract.currency,
            "conId": contract.conId,
            "localSymbol": contract.localSymbol,
            "primaryExchange": contract.primaryExchange,
        }

    def _persist_resolution(
        self,
        identifier: str,
        identifier_type: str,
        candidates: list[ContractDetails],
        selected: ContractDetails,
        instrument_id: str,
    ) -> None:
        meta_dir = Path(self.config.output_dir) / instrument_id / "meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        contract_payload = self._contract_payload(selected)
        resolution_payload = {
            "identifier": identifier,
            "identifier_type": identifier_type,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "candidate_count": len(candidates),
            "selected_conId": selected.contract.conId,
            "preferred_exchange": self.config.instruments.preferred_exchange,
            "currency": self.config.instruments.currency,
            "security_types": self.config.instruments.security_types,
            "candidates": [self._format_candidate(c) for c in candidates],
        }
        atomic_write(meta_dir / "contract_details.json", json.dumps(contract_payload, indent=2))
        atomic_write(meta_dir / "resolution.json", json.dumps(resolution_payload, indent=2))

    def _contract_payload(self, details: ContractDetails) -> dict[str, str | float | int | None]:
        contract = details.contract
        return {
            "conId": contract.conId,
            "symbol": contract.symbol,
            "localSymbol": contract.localSymbol,
            "secType": contract.secType,
            "exchange": contract.exchange,
            "primaryExchange": contract.primaryExchange,
            "currency": contract.currency,
            "timeZoneId": details.timeZoneId,
            "tradingHours": details.tradingHours,
            "liquidHours": details.liquidHours,
            "minTick": details.minTick,
        }