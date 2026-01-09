import pandas as pd

from ibkr_data_load.dqc import run_fundamentals_dqc
from ibkr_data_load.execution.fundamentals import _merge_append


def test_fundamentals_dqc_schema_and_nulls():
    df = pd.DataFrame(
        [
            {
                "conId": 123,
                "symbol": "ABC",
                "secType": "STK",
                "currency": "USD",
                "reportType": "ReportsFinStatements",
                "asOf_datetime_utc": "2024-01-01T00:00:00Z",
                "raw_payload_type": "xml",
                "raw_payload": "",
                "data_source": "ibkr",
                "dataset_id": "d",
                "run_id": "r",
            },
            {
                "conId": None,
                "symbol": "ABC",
                "secType": "STK",
                "currency": "USD",
                "reportType": None,
                "asOf_datetime_utc": "2024-01-02T00:00:00Z",
                "raw_payload_type": "xml",
                "raw_payload": "<xml/>",
                "data_source": "ibkr",
                "dataset_id": "d",
                "run_id": "r",
            },
        ]
    )
    result = run_fundamentals_dqc(
        df,
        required_columns=list(df.columns),
        key_columns=["conId", "symbol", "secType", "reportType"],
    )
    assert result.summary["schema_ok"] is True
    assert result.summary["raw_payload_empty_count"] == 1
    assert result.summary["key_null_counts"]["conId"] == 1
    assert result.summary["key_null_counts"]["reportType"] == 1


def test_fundamentals_append_dedup(tmp_path):
    existing = pd.DataFrame(
        [
            {
                "conId": 1,
                "symbol": "ABC",
                "secType": "STK",
                "currency": "USD",
                "reportType": "ReportsFinStatements",
                "asOf_datetime_utc": "2024-01-01T00:00:00Z",
                "raw_payload_type": "xml",
                "raw_payload": "<old/>",
                "data_source": "ibkr",
                "dataset_id": "d",
                "run_id": "r1",
            }
        ]
    )
    output_path = tmp_path / "fundamentals.csv"
    existing.to_csv(output_path, index=False)

    new_data = pd.DataFrame(
        [
            {
                "conId": 1,
                "symbol": "ABC",
                "secType": "STK",
                "currency": "USD",
                "reportType": "ReportsFinStatements",
                "asOf_datetime_utc": "2024-01-01T00:00:00Z",
                "raw_payload_type": "xml",
                "raw_payload": "<new/>",
                "data_source": "ibkr",
                "dataset_id": "d",
                "run_id": "r2",
            },
            {
                "conId": 1,
                "symbol": "ABC",
                "secType": "STK",
                "currency": "USD",
                "reportType": "ReportsFinStatements",
                "asOf_datetime_utc": "2024-01-02T00:00:00Z",
                "raw_payload_type": "xml",
                "raw_payload": "<next/>",
                "data_source": "ibkr",
                "dataset_id": "d",
                "run_id": "r2",
            },
        ]
    )
    merged = _merge_append(output_path, new_data)
    assert len(merged) == 2
    assert merged["asOf_datetime_utc"].is_monotonic_increasing