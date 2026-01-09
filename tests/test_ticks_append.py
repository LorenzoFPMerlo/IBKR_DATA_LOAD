import pandas as pd

from ibkr_data_load.config import (
    AppConfig,
    ConnectionConfig,
    HistoricalBarsConfig,
    HistoricalTicksConfig,
    InstrumentConfig,
    LoggingConfig,
    OptionsDiscoveryConfig,
    OptionsSnapshotConfig,
    PacingConfig,
    FundamentalsConfig,
)
from ibkr_data_load.execution.historical_ticks import HistoricalTicksExecutor


def test_ticks_append_dedup(tmp_path):
    config = AppConfig(
        output_dir=str(tmp_path),
        logging=LoggingConfig(level="INFO", json_logs=False),
        pacing=PacingConfig(
            min_interval_seconds=0.0,
            base_backoff_seconds=1.0,
            max_backoff_seconds=2.0,
            max_retries=1,
        ),
        connection=ConnectionConfig(
            host="127.0.0.1",
            port=4001,
            client_id=1,
            connect_timeout_seconds=5,
            max_retries=1,
            retry_backoff_seconds=1.0,
        ),
        instruments=InstrumentConfig(preferred_exchange=None, currency=None, security_types=["stock"]),
        historical_bars=HistoricalBarsConfig(
            enabled=True,
            bar_size="1 day",
            what_to_show="TRADES",
            use_rth=1,
            start_date=None,
            end_date=None,
            chunk_duration="1 Y",
            overlap_bars=5,
            min_request_interval_seconds=0.0,
            max_retries=1,
            format_date=1,
        ),
        options_discovery=OptionsDiscoveryConfig(cache_max_age_hours=24),
        options_snapshot=OptionsSnapshotConfig(
            enabled=False,
            max_contracts_per_batch=50,
            sleep_between_batches_seconds=1.0,
            snapshot_wait_seconds=1.0,
            max_expirations=6,
            strike_band_pct=0.3,
            strike_step=1,
            exchange="SMART",
            resume_overlap_batches=0,
        ),
        historical_ticks=HistoricalTicksConfig(
            enabled=True,
            start_date=None,
            end_date=None,
            chunk_seconds=3600,
            max_ticks_per_request=1000,
            min_request_interval_seconds=0.0,
            max_retries=1,
            resume_overlap_seconds=0,
        ),
        fundamentals=FundamentalsConfig(enabled=True, default_report_type="ReportsFinStatements"),
    )
    executor = HistoricalTicksExecutor(config=config, run_id="test")
    dataset = "ticks"

    existing = pd.DataFrame(
        {
            "time": ["2024-01-01T00:00:00+00:00"],
            "price": [1.0],
            "size": [10],
            "conId": [1],
            "dataset_id": [dataset],
        }
    )
    output_path = tmp_path / "ticks.csv"
    existing.to_csv(output_path, index=False)

    new_data = pd.DataFrame(
        {
            "time": ["2024-01-01T00:00:00+00:00", "2024-01-01T00:00:01+00:00"],
            "price": [1.0, 1.1],
            "size": [10, 5],
            "conId": [1, 1],
            "dataset_id": [dataset, dataset],
        }
    )

    merged = executor._merge_append(output_path, new_data, dataset)
    assert len(merged) == 2
    assert list(merged["time"]) == sorted(merged["time"])