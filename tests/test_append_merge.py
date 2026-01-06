import pandas as pd

from ibkr_data_load.config import AppConfig, ConnectionConfig, HistoricalBarsConfig, InstrumentConfig, LoggingConfig
from ibkr_data_load.execution.historical_bars import HistoricalBarsExecutor


def test_append_merge_idempotent(tmp_path):
    config = AppConfig(
        output_dir=str(tmp_path),
        logging=LoggingConfig(level="INFO", json_logs=False),
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
    )
    executor = HistoricalBarsExecutor(config=config, run_id="test")

    dataset = "abc"
    existing = pd.DataFrame(
        {
            "time": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"], utc=True),
            "open": [1, 1, 1],
            "high": [1, 1, 1],
            "low": [1, 1, 1],
            "close": [1, 1, 1],
            "volume": [10, 10, 10],
            "wap": [1, 1, 1],
            "barCount": [1, 1, 1],
            "conId": [1, 1, 1],
            "dataset_id": [dataset, dataset, dataset],
        }
    )
    output_path = tmp_path / "historical.csv"
    existing.to_csv(output_path, index=False)

    new_data = pd.DataFrame(
        {
            "time": pd.to_datetime(["2024-01-03", "2024-01-04"], utc=True),
            "open": [1, 1],
            "high": [1, 1],
            "low": [1, 1],
            "close": [1, 1],
            "volume": [10, 10],
            "wap": [1, 1],
            "barCount": [1, 1],
            "conId": [1, 1],
            "dataset_id": [dataset, dataset],
        }
    )

    merged = executor._merge_append(output_path, new_data, dataset)
    assert len(merged) == 4
    assert merged["time"].is_monotonic_increasing