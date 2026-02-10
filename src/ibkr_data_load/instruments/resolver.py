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


@dataclass(frozen=True)
class ResolutionOverrides:
    sec_type: str | None = None
    exchange: str | None = None
    primary_exchange: str | None = None
    currency: str | None = None


@dataclass(frozen=True)
class ResolutionPolicy:
    stock_like_sec_types: tuple[str, ...] = ("STK", "ETF")
    index_sec_types: tuple[str, ...] = ("IND",)
    allowed_ticker_sec_types: tuple[str, ...] = ("STK", "ETF", "IND", "FUT")
    stock_like_primary_exchanges: tuple[str, ...] = ("ARCA", "NYSE", "NASDAQ", "ISLAND")
    index_exchanges: tuple[str, ...] = ("CBOE", "SMART")
    default_exchange: str = "SMART"
    default_currency: str = "USD"


@dataclass(frozen=True)
class CandidateDecision:
    details: ContractDetails
    keep: bool
    score: int
    reasons: list[str]


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

    WELL_KNOWN_TICKERS: dict[str, dict[str, str]] = {
        "SPY": {"sec_type": "STK", "exchange": "SMART", "currency": "USD", "primary_exchange": "ARCA"},
        "SPX": {"sec_type": "IND", "exchange": "CBOE", "currency": "USD", "primary_exchange": "CBOE"},
        "VIX": {"sec_type": "IND", "exchange": "CBOE", "currency": "USD", "primary_exchange": "CBOE"},
    }

    def resolve(
        self,
        ib: IB,
        identifier: str,
        identifier_type: str,
        overrides: ResolutionOverrides | None = None,
    ) -> ResolvedInstrument:
        if identifier_type not in {"isin", "ticker"}:
            raise ValueError("identifier_type must be 'isin' or 'ticker'")
        if identifier_type == "isin":
            candidates = self._resolve_isin(ib, identifier)
            decisions = self._apply_hints(candidates)
        else:
            candidates = self._resolve_ticker(ib, identifier, overrides)
            decisions = self._select_ticker_candidates(identifier, candidates, overrides)

        filtered = [decision for decision in decisions if decision.keep]
        if not filtered:
            self._persist_resolution(identifier, identifier_type, candidates, None, "unresolved", decisions, overrides)
            raise InstrumentNotFoundError(f"No contract found for {identifier}")
        top_score = max(decision.score for decision in filtered)
        winners = [decision for decision in filtered if decision.score == top_score]
        if len(winners) > 1:
            candidate_payload = [self._format_candidate(decision.details) for decision in winners]
            self._persist_resolution(identifier, identifier_type, candidates, None, "ambiguous", decisions, overrides)
            raise AmbiguousInstrumentError(
                f"Ambiguous instrument for {identifier}: {json.dumps(candidate_payload, indent=2)}"
            )

        selected = winners[0].details
        instrument_id = self._canonical_id(selected.contract)
        self._persist_resolution(identifier, identifier_type, candidates, selected, instrument_id, decisions, overrides)
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

    def _resolve_ticker(self, ib: IB, ticker: str, overrides: ResolutionOverrides | None) -> list[ContractDetails]:
        candidates: list[ContractDetails] = []
        for sec_type in self._ticker_sec_types(overrides):
            contract = self._contract_for_type(ticker, sec_type, overrides)
            if contract is None:
                continue
            details = ib.reqContractDetails(contract)
            candidates.extend(details)
        return self._dedupe_contracts(candidates)
    def _ticker_sec_types(self, overrides: ResolutionOverrides | None) -> list[str]:
        policy = ResolutionPolicy()
        if overrides and overrides.sec_type:
            return [overrides.sec_type.upper()]
        configured = [SEC_TYPE_MAP.get(sec, sec).upper() for sec in self.config.instruments.security_types]
        allowed = set(policy.allowed_ticker_sec_types)
        filtered = [value for value in configured if value in allowed]
        if not filtered:
            filtered = list(policy.allowed_ticker_sec_types)
        return filtered

    def _contract_for_type(self, ticker: str, sec_type: str, overrides: ResolutionOverrides | None) -> Contract | None:
        policy = ResolutionPolicy()
        exchange = (overrides.exchange if overrides and overrides.exchange else self.config.instruments.preferred_exchange) or policy.default_exchange
        currency = (overrides.currency if overrides and overrides.currency else self.config.instruments.currency) or policy.default_currency
        sec_type = SEC_TYPE_MAP.get(sec_type, sec_type).upper()
        if sec_type in {"STK", "ETF"}:
            return Stock(ticker, exchange, currency)
        if sec_type == "IND":
            index_exchange = exchange
            if ticker.upper() in {"SPX", "VIX"} and not (overrides and overrides.exchange):
                index_exchange = "CBOE"
            return Index(ticker, index_exchange)
        if sec_type == "FUT":
            return Contract(symbol=ticker, secType="FUT", exchange=exchange)
        if sec_type == "OPT":
            return Option(ticker, "", 0, "", exchange)
        if sec_type == "BOND":
            return Bond()
        return None

    def _dedupe_contracts(self, candidates: list[ContractDetails]) -> list[ContractDetails]:
        unique: dict[tuple[int, str, str, str], ContractDetails] = {}
        for details in candidates:
            contract = details.contract
            key = (int(contract.conId or 0), contract.secType or "", contract.exchange or "", contract.localSymbol or "")
            unique[key] = details
        return list(unique.values())

    def _apply_hints(self, candidates: Iterable[ContractDetails]) -> list[CandidateDecision]:
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
        return [CandidateDecision(details=value, keep=True, score=0, reasons=[]) for value in filtered]

    def _select_ticker_candidates(
        self,
        ticker: str,
        candidates: list[ContractDetails],
        overrides: ResolutionOverrides | None,
    ) -> list[CandidateDecision]:
        policy = ResolutionPolicy()
        ticker_key = ticker.upper()
        known_defaults = dict(self.WELL_KNOWN_TICKERS.get(ticker_key, {}))
        if overrides:
            override_map = {
                "sec_type": overrides.sec_type,
                "exchange": overrides.exchange,
                "currency": overrides.currency,
                "primary_exchange": overrides.primary_exchange,
            }
            for key, value in override_map.items():
                if value:
                    known_defaults[key] = value.upper()

        sec_types = {c.contract.secType.upper() for c in candidates if c.contract.secType}
        has_index = "IND" in sec_types
        decisions: list[CandidateDecision] = []
        for details in candidates:
            contract = details.contract
            sec_type = (contract.secType or "").upper()
            exchange = (contract.exchange or "").upper()
            primary_exchange = (contract.primaryExchange or "").upper()
            currency = (contract.currency or "").upper()
            long_name = ((getattr(details, "longName", None) or "") + " " + (getattr(details, "marketName", None) or "")).upper()
            reasons: list[str] = []
            score = 0
            keep = True

            if sec_type == "BOND" and not (overrides and (overrides.sec_type or "").upper() == "BOND"):
                keep = False
                reasons.append("rejected: bond contracts require explicit --sec-type BOND")

            if keep and sec_type not in policy.allowed_ticker_sec_types and sec_type != "BOND":
                keep = False
                reasons.append(f"rejected: secType {sec_type} not in default ticker universe")

            if keep and "BOND" in long_name and not (overrides and (overrides.sec_type or "").upper() == "BOND"):
                keep = False
                reasons.append("rejected: bond-like issuer/description")

            requested_sec_type = known_defaults.get("sec_type")
            if keep and requested_sec_type:
                if sec_type == requested_sec_type:
                    score += 100
                    reasons.append(f"preferred secType={requested_sec_type}")
                else:
                    keep = False
                    reasons.append(f"rejected: secType mismatch expected {requested_sec_type}")

            if keep and ticker_key in {"SPX", "VIX"} and has_index and sec_type != "IND":
                keep = False
                reasons.append("rejected: index contract preferred over stock-like match")

            requested_currency = known_defaults.get("currency") or policy.default_currency
            if keep:
                if currency == requested_currency:
                    score += 30
                    reasons.append(f"preferred currency={requested_currency}")
                elif known_defaults.get("currency"):
                    keep = False
                    reasons.append(f"rejected: currency mismatch expected {requested_currency}")

            requested_exchange = known_defaults.get("exchange")
            if keep and requested_exchange:
                if exchange == requested_exchange or (requested_exchange == "SMART" and exchange == "SMART"):
                    score += 20
                    reasons.append(f"preferred exchange={requested_exchange}")

            requested_primary_exchange = known_defaults.get("primary_exchange")
            if keep and requested_primary_exchange:
                if primary_exchange == requested_primary_exchange:
                    score += 20
                    reasons.append(f"preferred primaryExchange={requested_primary_exchange}")
                elif sec_type in policy.stock_like_sec_types and primary_exchange:
                    keep = False
                    reasons.append(f"rejected: primaryExchange mismatch expected {requested_primary_exchange}")

            if keep and sec_type in policy.stock_like_sec_types and not requested_primary_exchange:
                if primary_exchange in policy.stock_like_primary_exchanges:
                    score += 10
                    reasons.append("preferred stock primary exchange")

            if keep and sec_type in policy.index_sec_types and not requested_exchange:
                if exchange in policy.index_exchanges:
                    score += 10
                    reasons.append("preferred index exchange")

            decisions.append(CandidateDecision(details=details, keep=keep, score=score, reasons=reasons))
        return decisions


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
        selected: ContractDetails | None,
        instrument_id: str,
        decisions: list[CandidateDecision],
        overrides: ResolutionOverrides | None,
    ) -> None:
        meta_dir = Path(self.config.output_dir) / instrument_id / "meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        contract_payload = self._contract_payload(selected) if selected is not None else {}
        applied_policy = self.WELL_KNOWN_TICKERS.get(identifier.upper(), {}) if identifier_type == "ticker" else {}
        overrides_payload = {
            "sec_type": overrides.sec_type if overrides else None,
            "exchange": overrides.exchange if overrides else None,
            "currency": overrides.currency if overrides else None,
            "primary_exchange": overrides.primary_exchange if overrides else None,
        }
        rejected = [
            {
                **self._format_candidate(decision.details),
                "reasons": decision.reasons,
            }
            for decision in decisions
            if not decision.keep
        ]
        resolution_payload = {
            "input": {"identifier": identifier, "identifier_type": identifier_type},
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "candidate_count": len(candidates),
            "filters_applied": {
                "policy": applied_policy,
                "overrides": overrides_payload,
                "default_security_types": self._ticker_sec_types(overrides),
            },
            "chosen_contract": self._format_candidate(selected) if selected is not None else None,
            "rejected_candidates_top5": rejected[:5],
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