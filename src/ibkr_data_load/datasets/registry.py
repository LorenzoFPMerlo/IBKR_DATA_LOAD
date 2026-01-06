from __future__ import annotations

"""Dataset registry definitions.

Capability probe tags (finite set):
- security_def
- market_data
- historical_data
- options_data
- depth_data
- fundamentals
- news
- account_data
"""

from dataclasses import dataclass, field
from typing import Any, Iterable

STATUS_VALUES = {"implemented", "stub", "unimplemented"}
CAPABILITY_TAGS = {
    "security_def",
    "market_data",
    "historical_data",
    "options_data",
    "depth_data",
    "fundamentals",
    "news",
    "account_data",
}


@dataclass(frozen=True)
class DatasetDefinition:
    name: str
    instrument_types: tuple[str, ...]
    request_type: str
    parameter_schema: dict[str, str]
    default_parameters: dict[str, Any]
    chunking_strategy: str
    storage_schema: list[str]
    incremental_key: str
    prerequisites: tuple[str, ...] = ()
    capability_probe: str = ""
    cost_estimate: int = 1
    status: str = "implemented"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUS_VALUES:
            raise ValueError(f"Invalid status: {self.status}")
        if self.status != "implemented" and not self.reason:
            raise ValueError(
                f"Dataset {self.name} has status={self.status} without reason"
            )
        if self.capability_probe not in CAPABILITY_TAGS:
            raise ValueError(
                f"Dataset {self.name} has invalid capability_probe={self.capability_probe}"
            )


@dataclass
class DatasetRegistry:
    datasets: dict[str, DatasetDefinition] = field(default_factory=dict)

    def add(self, definition: DatasetDefinition) -> None:
        self.datasets[definition.name] = definition

    def list(self) -> Iterable[DatasetDefinition]:
        return self.datasets.values()

    def get(self, name: str) -> DatasetDefinition:
        return self.datasets[name]

    def as_records(self) -> list[dict[str, Any]]:
        return [
            {
                "dataset_name": dataset.name,
                "request_type": dataset.request_type,
                "instrument_types": list(dataset.instrument_types),
                "status": dataset.status,
                "capability_probe": dataset.capability_probe,
                "reason": dataset.reason,
            }
            for dataset in self.list()
        ]

    @classmethod
    def default(cls) -> "DatasetRegistry":
        registry = cls()
        registry.add(
            DatasetDefinition(
                name="matching_symbols",
                instrument_types=("stock", "etf", "index", "future", "option", "bond"),
                request_type="reqMatchingSymbols",
                parameter_schema={"pattern": "str"},
                default_parameters={"pattern": ""},
                chunking_strategy="none",
                storage_schema=["symbol", "secType", "primaryExchange", "currency"],
                incremental_key="symbol",
                capability_probe="security_def",
                cost_estimate=1,
                status="stub",
                reason="Discovery flow not wired to connector yet.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="contract_details",
                instrument_types=(
                    "stock",
                    "etf",
                    "index",
                    "future",
                    "option",
                    "bond",
                    "forex",
                    "cfd",
                    "mutual_fund",
                    "warrant",
                    "structured_product",
                ),
                request_type="reqContractDetails",
                parameter_schema={"conId": "int", "symbol": "str"},
                default_parameters={},
                chunking_strategy="none",
                storage_schema=[
                    "conId",
                    "symbol",
                    "secType",
                    "exchange",
                    "primaryExchange",
                    "currency",
                    "localSymbol",
                    "tradingClass",
                    "timeZoneId",
                    "tradingHours",
                    "liquidHours",
                    "minTick",
                    "multiplier",
                ],
                incremental_key="conId",
                capability_probe="security_def",
                cost_estimate=1,
                status="stub",
                reason="Requires connector integration.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="market_rule",
                instrument_types=("stock", "etf", "index", "future", "option"),
                request_type="reqMarketRule",
                parameter_schema={"marketRuleId": "int"},
                default_parameters={},
                chunking_strategy="none",
                storage_schema=["marketRuleId", "priceIncrements"],
                incremental_key="marketRuleId",
                capability_probe="security_def",
                cost_estimate=1,
                status="stub",
                reason="Requires marketRuleId from contract details.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="option_chain_discovery",
                instrument_types=("stock", "etf", "index"),
                request_type="reqSecDefOptParams",
                parameter_schema={"symbol": "str", "exchange": "str"},
                default_parameters={"exchange": "SMART"},
                chunking_strategy="none",
                storage_schema=[
                    "underlying_conId",
                    "tradingClass",
                    "exchange",
                    "expirations",
                    "strikes",
                    "multiplier",
                ],
                incremental_key="underlying_conId",
                capability_probe="options_data",
                cost_estimate=2,
                status="implemented",
                reason="Discovery flow not wired to connector yet.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="futures_chain_discovery",
                instrument_types=("future",),
                request_type="reqContractDetails",
                parameter_schema={"symbol": "str", "exchange": "str"},
                default_parameters={"exchange": "SMART"},
                chunking_strategy="none",
                storage_schema=[
                    "symbol",
                    "exchange",
                    "currency",
                    "lastTradeDateOrContractMonth",
                    "conId",
                ],
                incremental_key="conId",
                capability_probe="security_def",
                cost_estimate=2,
                status="stub",
                reason="Futures chain discovery requires contract search logic.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="live_quote_snapshot",
                instrument_types=(
                    "stock",
                    "etf",
                    "index",
                    "future",
                    "option",
                    "forex",
                    "cfd",
                ),
                request_type="reqMktData(snapshot)",
                parameter_schema={"snapshot": "bool"},
                default_parameters={"snapshot": True},
                chunking_strategy="by_contract_batches",
                storage_schema=[
                    "timestamp_utc",
                    "bid",
                    "ask",
                    "last",
                    "bidSize",
                    "askSize",
                    "lastSize",
                    "volume",
                    "open",
                    "high",
                    "low",
                    "close",
                    "halted",
                ],
                incremental_key="timestamp_utc",
                capability_probe="market_data",
                cost_estimate=2,
                status="stub",
                reason="Requires market data subscription handling.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="live_top_of_book",
                instrument_types=(
                    "stock",
                    "etf",
                    "index",
                    "future",
                    "option",
                    "forex",
                ),
                request_type="reqMktData(streaming)",
                parameter_schema={"snapshot": "bool"},
                default_parameters={"snapshot": False},
                chunking_strategy="streaming",
                storage_schema=[
                    "timestamp_utc",
                    "bid",
                    "ask",
                    "last",
                    "bidSize",
                    "askSize",
                    "lastSize",
                ],
                incremental_key="timestamp_utc",
                capability_probe="market_data",
                cost_estimate=4,
                status="stub",
                reason="Streaming engine not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="tick_by_tick_trades_live",
                instrument_types=("stock", "etf", "index", "future", "option", "forex"),
                request_type="reqTickByTickData",
                parameter_schema={"tickType": "str"},
                default_parameters={"tickType": "Last"},
                chunking_strategy="streaming",
                storage_schema=["timestamp_utc", "price", "size", "exchange", "conId"],
                incremental_key="timestamp_utc",
                capability_probe="market_data",
                cost_estimate=5,
                status="stub",
                reason="Tick-by-tick streaming not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="market_depth",
                instrument_types=("stock", "etf", "index", "future", "option"),
                request_type="reqMktDepth/reqMktDepthExchanges",
                parameter_schema={"numRows": "int"},
                default_parameters={"numRows": 10},
                chunking_strategy="streaming",
                storage_schema=["timestamp_utc", "position", "operation", "side", "price", "size", "marketMaker",],
                incremental_key="timestamp_utc",
                capability_probe="depth_data",
                cost_estimate=6,
                status="stub",
                reason="Market depth streaming not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="historical_bars",
                instrument_types=("stock", "etf", "index", "future", "option"),
                request_type="reqHistoricalData",
                parameter_schema={
                    "barSize": "str",
                    "duration": "str",
                    "whatToShow": "str",
                    "useRTH": "int",
                },
                default_parameters={
                    "barSize": "1 day",
                    "duration": "5 Y",
                    "whatToShow": "TRADES",
                    "useRTH": 1,
                },
                chunking_strategy="time_windows",
                storage_schema=[
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "wap",
                    "barCount",
                ],
                incremental_key="time",
                capability_probe="historical_data",
                cost_estimate=3,
            )
        )
        registry.add(
            DatasetDefinition(
                name="historical_ticks_trades",
                instrument_types=("stock", "etf", "index", "future", "option", "forex"),
                request_type="reqHistoricalTicks(TRADES)",
                parameter_schema={"start": "datetime", "end": "datetime"},
                default_parameters={},
                chunking_strategy="time_windows",
                storage_schema=["time", "price", "size", "exchange", "specialConditions", "conId", "dataset_id", "run_id"],
                incremental_key="time",
                capability_probe="historical_data",
                cost_estimate=5,
                status="implemented",
            )
        )
        registry.add(
            DatasetDefinition(
                name="historical_ticks_bid_ask",
                instrument_types=("stock", "etf", "index", "future", "option", "forex"),
                request_type="reqHistoricalTicks(BID_ASK)",
                parameter_schema={"start": "datetime", "end": "datetime"},
                default_parameters={},
                chunking_strategy="time_windows",
                storage_schema=["time", "bid", "ask", "bidSize", "askSize", "conId", "dataset_id", "run_id"],
                incremental_key="time",
                capability_probe="historical_data",
                cost_estimate=5,
                status="implemented",
            )
        )
        registry.add(
            DatasetDefinition(
                name="historical_ticks_midpoint",
                instrument_types=("stock", "etf", "index", "future", "option", "forex"),
                request_type="reqHistoricalTicks(MIDPOINT)",
                parameter_schema={"start": "datetime", "end": "datetime"},
                default_parameters={},
                chunking_strategy="time_windows",
                storage_schema=["time", "midpoint"],
                incremental_key="time",
                capability_probe="historical_data",
                cost_estimate=5,
                status="stub",
                reason="Historical tick retrieval not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="option_surface_snapshot_daily",
                instrument_types=("stock", "etf", "index"),
                request_type="reqSecDefOptParams+reqMktDataSnapshot",
                parameter_schema={
                    "snapshotDate": "date",
                    "max_contracts_per_batch": "int",
                },
                default_parameters={"snapshotDate": "today", "max_contracts_per_batch": 50},
                chunking_strategy="by_expiration_and_batches",
                storage_schema=[
                    "snapshot_datetime_utc",
                    "underlying_conId",
                    "option_conId",
                    "expiration",
                    "strike",
                    "right",
                    "multiplier",
                    "bid",
                    "ask",
                    "last",
                    "bidSize",
                    "askSize",
                    "volume",
                    "openInterest",
                    "impliedVolatility",
                    "delta",
                    "gamma",
                    "vega",
                    "theta",
                    "pvDividend",
                    "undPrice",
                    "tradingClass",
                    "exchange",
                    "currency",
                    "data_source",
                    "dataset_id",
                    "run_id",
                ],
                incremental_key="snapshot_datetime_utc",
                prerequisites=("option_chain_discovery",),
                capability_probe="options_data",
                cost_estimate=5,
                status="implemented",
            )
        )
        registry.add(
            DatasetDefinition(
                name="option_contract_bars",
                instrument_types=("option",),
                request_type="reqHistoricalData",
                parameter_schema={
                    "barSize": "str",
                    "duration": "str",
                    "whatToShow": "str",
                },
                default_parameters={
                    "barSize": "1 day",
                    "duration": "1 Y",
                    "whatToShow": "TRADES",
                },
                chunking_strategy="time_windows",
                storage_schema=[
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "wap",
                    "barCount",
                ],
                incremental_key="time",
                capability_probe="options_data",
                cost_estimate=4,
                status="stub",
                reason="Option contract history not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="option_contract_tick_by_tick",
                instrument_types=("option",),
                request_type="reqTickByTickData",
                parameter_schema={"tickType": "str"},
                default_parameters={"tickType": "Last"},
                chunking_strategy="streaming",
                storage_schema=["timestamp_utc", "price", "size", "exchange"],
                incremental_key="timestamp_utc",
                capability_probe="options_data",
                cost_estimate=5,
                status="stub",
                reason="Option tick-by-tick not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="fundamentals_report",
                instrument_types=("stock", "etf", "index"),
                request_type="reqFundamentalData",
                parameter_schema={"reportType": "str"},
                default_parameters={"reportType": "ReportsFinSummary"},
                chunking_strategy="none",
                storage_schema=["conId", "symbol", "report_type", "asof", "payload"],
                incremental_key="asof",
                capability_probe="fundamentals",
                cost_estimate=2,
                status="stub",
                reason="Fundamentals retrieval not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="news_providers",
                instrument_types=("stock", "etf", "index", "future", "option"),
                request_type="reqNewsProviders",
                parameter_schema={},
                default_parameters={},
                chunking_strategy="none",
                storage_schema=["providerCode", "providerName"],
                incremental_key="providerCode",
                capability_probe="news",
                cost_estimate=1,
                status="stub",
                reason="News providers retrieval not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="historical_news",
                instrument_types=("stock", "etf", "index", "future", "option"),
                request_type="reqHistoricalNews",
                parameter_schema={"start": "datetime", "end": "datetime"},
                default_parameters={},
                chunking_strategy="by_page",
                storage_schema=["time", "providerCode", "articleId", "headline"],
                incremental_key="time",
                capability_probe="news",
                cost_estimate=3,
                status="stub",
                reason="Historical news retrieval not implemented.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="corporate_actions",
                instrument_types=("stock", "etf", "index"),
                request_type="(no direct IB API)",
                parameter_schema={},
                default_parameters={},
                chunking_strategy="none",
                storage_schema=["time", "action", "details"],
                incremental_key="time",
                capability_probe="market_data",
                cost_estimate=1,
                status="unimplemented",
                reason="No reliable IB API endpoint for corporate actions.",
            )
        )
        registry.add(
            DatasetDefinition(
                name="account_positions",
                instrument_types=("stock", "etf", "index", "future", "option"),
                request_type="reqPositions/reqAccountUpdates",
                parameter_schema={},
                default_parameters={},
                chunking_strategy="none",
                storage_schema=["account", "conId", "position", "avgCost"],
                incremental_key="conId",
                capability_probe="account_data",
                cost_estimate=1,
                status="unimplemented",
                reason="Out of scope: instrument-centric loader.",
            )
        )
        return registry