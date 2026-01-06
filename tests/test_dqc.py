import pytest

pd = pytest.importorskip("pandas")

from ibkr_data_load.dqc import run_bars_dqc


def test_dqc_gap_detection():
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-04"], utc=True
            ),
            "open": [1, 2, 3],
            "high": [1, 2, 3],
            "low": [1, 2, 3],
            "close": [1, 2, 3],
            "volume": [10, 10, 10],
            "barCount": [1, 1, 1],
            "conId": [1, 1, 1],
            "dataset_id": ["d", "d", "d"],
        }
    )
    result = run_bars_dqc(
        df,
        time_column="time",
        key_columns=["time", "conId", "dataset_id"],
        expected_delta=pd.Timedelta(days=1),
    )
    assert result.summary["gap_count"] == 1

def test_dqc_ohlc_outliers_and_duplicates():
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2024-01-01", "2024-01-01"], utc=True),
            "open": [10, 10],
            "high": [9, 12],
            "low": [8, 9],
            "close": [10, 10],
            "volume": [100, 100],
            "barCount": [1, 1],
            "conId": [1, 1],
            "dataset_id": ["d", "d"],
        }
    )
    result = run_bars_dqc(
        df,
        time_column="time",
        key_columns=["time", "conId", "dataset_id"],
        expected_delta=pd.Timedelta(days=1),
    )
    assert result.summary["duplicate_count"] == 2
    assert result.summary["outlier_count"] == 1