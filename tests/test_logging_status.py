import logging

from ibkr_data_load.app import LoadApp
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
from ibkr_data_load.datasets.registry import DatasetRegistry


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class DummyResult:
    def __init__(self) -> None:
        self.row_count = 1
        self.dataset_id = "dummy"


def _build_config() -> AppConfig:
    return AppConfig(
        output_dir="data",
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
        instruments=InstrumentConfig(
            preferred_exchange=None, currency=None, security_types=["stock"]
        ),
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
            enabled=False,
            start_date=None,
            end_date=None,
            chunk_seconds=3600,
            max_ticks_per_request=1000,
            min_request_interval_seconds=0.0,
            max_retries=1,
            resume_overlap_seconds=0,
        ),
        fundamentals=FundamentalsConfig(
            enabled=True, default_report_type="ReportsFinStatements"
        ),
    )


def test_job_status_logging():
    logger = logging.getLogger("job_status_test")
    logger.setLevel(logging.INFO)
    handler = ListHandler()
    logger.addHandler(handler)
    logger.propagate = False

    app = LoadApp(config=_build_config(), registry=DatasetRegistry.default())
    run_summary = {"datasets": []}
    app._run_job(
        logger,
        run_summary,
        run_id="run-1",
        instrument_id="inst-1",
        dataset_name="dummy_dataset",
        runner=lambda: DummyResult(),
    )

    statuses = [getattr(record, "status", None) for record in handler.records]
    assert "started" in statuses
    assert "succeeded" in statuses
    logger.removeHandler(handler)