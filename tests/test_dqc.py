import pytest

pd = pytest.importorskip("pandas")

from ibkr_data_load.dqc import run_dqc


def test_dqc_gap_detection():
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-04"], utc=True
            ),
            "value": [1, 2, 3],
            "conId": [1, 1, 1],
        }
    )
    result = run_dqc(
        df,
        time_column="time",
        key_columns=["time", "conId"],
        expected_delta=pd.Timedelta(days=1),
    )
    assert result.summary["gap_count"] == 1