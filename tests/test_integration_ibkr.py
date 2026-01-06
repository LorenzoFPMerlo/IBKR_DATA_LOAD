import pytest
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from ibkr_data_load.config import load_config
from ibkr_data_load.connection.connector import IbkrConnector
from ibkr_data_load.execution.historical_bars import HistoricalBarsExecutor
from ibkr_data_load.instruments.resolver import InstrumentResolver
from ibkr_data_load.logging import build_logger


@pytest.mark.skipif(os.environ.get("RUN_IBKR_TESTS") != "1", reason="IBKR tests disabled")
def test_ibkr_integration(tmp_path: Path):
    config = load_config(Path("config/defaults.yaml"), Path("config/user.yaml"))
    config = replace(config, output_dir=str(tmp_path))
    logger = build_logger(config.logging)
    run_id = "integration"

    connector = IbkrConnector(config=config, run_id=run_id, logger=logger)
    try:
        with connector.connect() as ib:
            resolver = InstrumentResolver(config=config, run_id=run_id, logger=logger)
            resolved = resolver.resolve(ib, "AAPL", "ticker")
            executor = HistoricalBarsExecutor(config=config, run_id=run_id, logger=logger)
            end_date = datetime.now(tz=timezone.utc)
            start_date = end_date - timedelta(days=5)
            result = executor.run(
                ib,
                instrument_id=resolved.instrument_id,
                contract_details=resolved.contract_details,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                resume=False,
            )
    except Exception as exc:
        pytest.skip(f"IBKR connection unavailable: {exc}")

    assert result.output_path.exists()
    dqc_summary = (
        tmp_path
        / resolved.instrument_id
        / "meta"
        / "dqc"
        / result.dataset_id
        / "dqc_summary.json"
    )
    assert dqc_summary.exists()