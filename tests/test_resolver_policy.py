from ib_insync import Contract, ContractDetails

from ibkr_data_load.config import (
    AppConfig,
    ConnectionConfig,
    FundamentalsConfig,
    HistoricalBarsConfig,
    HistoricalTicksConfig,
    InstrumentConfig,
    LoggingConfig,
    OptionsDiscoveryConfig,
    OptionsSnapshotConfig,
    PacingConfig,
)
from ibkr_data_load.instruments.resolver import InstrumentResolver


def _build_config() -> AppConfig:
    return AppConfig(
        output_dir="data",
        logging=LoggingConfig(level="INFO", json_logs=False),
        pacing=PacingConfig(min_interval_seconds=0.0, base_backoff_seconds=1.0, max_backoff_seconds=2.0, max_retries=1),
        connection=ConnectionConfig(host="127.0.0.1", port=4001, client_id=1, connect_timeout_seconds=5, max_retries=1, retry_backoff_seconds=1.0),
        instruments=InstrumentConfig(preferred_exchange=None, currency=None, security_types=["stock", "etf", "index", "future", "bond"]),
        historical_bars=HistoricalBarsConfig(enabled=True, bar_size="1 day", what_to_show="TRADES", use_rth=1, start_date=None, end_date=None, chunk_duration="1 Y", overlap_bars=5, min_request_interval_seconds=0.0, max_retries=1, format_date=1),
        options_discovery=OptionsDiscoveryConfig(cache_max_age_hours=24),
        options_snapshot=OptionsSnapshotConfig(enabled=False, max_contracts_per_batch=50, sleep_between_batches_seconds=1.0, snapshot_wait_seconds=1.0, max_expirations=6, strike_band_pct=0.3, strike_step=1, exchange="SMART", resume_overlap_batches=0),
        historical_ticks=HistoricalTicksConfig(enabled=False, start_date=None, end_date=None, chunk_seconds=3600, max_ticks_per_request=1000, min_request_interval_seconds=0.0, max_retries=1, resume_overlap_seconds=0),
        fundamentals=FundamentalsConfig(enabled=True, default_report_type="ReportsFinStatements"),
    )


def _details(symbol: str, sec_type: str, exchange: str, currency: str, con_id: int, primary_exchange: str = "", long_name: str = "") -> ContractDetails:
    return ContractDetails(
        contract=Contract(symbol=symbol, secType=sec_type, exchange=exchange, currency=currency, conId=con_id, primaryExchange=primary_exchange),
        longName=long_name,
    )


def test_spy_default_policy_prefers_usd_arca_and_rejects_bonds() -> None:
    resolver = InstrumentResolver(config=_build_config(), run_id="t")
    candidates = [
        _details("SPY", "STK", "SMART", "USD", 756733, primary_exchange="ARCA", long_name="SPDR S&P 500 ETF TRUST"),
        _details("SPY", "STK", "SMART", "AUD", 111, primary_exchange="ASX", long_name="SPY AU"),
        _details("SPY", "BOND", "SMART", "USD", 222, primary_exchange="", long_name="SPY Agro Industries Ltd Bond"),
    ]

    decisions = resolver._select_ticker_candidates("SPY", candidates, overrides=None)
    kept = [d for d in decisions if d.keep]

    assert len(kept) == 1
    assert kept[0].details.contract.conId == 756733
    assert any((not d.keep) and d.details.contract.secType == "BOND" for d in decisions)


def test_spx_policy_prefers_index_contract() -> None:
    resolver = InstrumentResolver(config=_build_config(), run_id="t")
    candidates = [
        _details("SPX", "STK", "SMART", "USD", 9001, primary_exchange="NYSE", long_name="SPX stock-like"),
        _details("SPX", "IND", "CBOE", "USD", 416904, primary_exchange="CBOE", long_name="S&P 500 Index"),
    ]

    decisions = resolver._select_ticker_candidates("SPX", candidates, overrides=None)
    kept = [d for d in decisions if d.keep]

    assert len(kept) == 1
    assert kept[0].details.contract.secType == "IND"
    assert kept[0].details.contract.conId == 416904


def test_vix_policy_prefers_index_contract() -> None:
    resolver = InstrumentResolver(config=_build_config(), run_id="t")
    candidates = [
        _details("VIX", "STK", "SMART", "USD", 7001, primary_exchange="NASDAQ", long_name="VIX stock-like"),
        _details("VIX", "IND", "CBOE", "USD", 117600, primary_exchange="CBOE", long_name="CBOE Volatility Index"),
    ]

    decisions = resolver._select_ticker_candidates("VIX", candidates, overrides=None)
    kept = [d for d in decisions if d.keep]

    assert len(kept) == 1
    assert kept[0].details.contract.secType == "IND"
    assert kept[0].details.contract.conId == 117600