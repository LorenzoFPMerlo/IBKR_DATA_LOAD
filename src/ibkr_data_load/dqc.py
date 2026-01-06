from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass
class DqcResult:
    summary: dict
    gaps: pd.DataFrame
    duplicates: pd.DataFrame
    nulls: pd.DataFrame


def check_duplicates(df: pd.DataFrame, key_columns: Iterable[str]) -> pd.DataFrame:
    mask = df.duplicated(subset=list(key_columns), keep=False)
    return df.loc[mask].copy()


def check_nulls(df: pd.DataFrame) -> pd.DataFrame:
    counts = df.isna().sum()
    total = len(df)
    summary = pd.DataFrame(
        {
            "column": counts.index,
            "null_count": counts.values,
            "null_pct": (counts.values / total) * 100 if total else 0,
        }
    )
    return summary


def check_gaps(timestamps: pd.Series, expected_delta: pd.Timedelta) -> pd.DataFrame:
    sorted_ts = timestamps.sort_values().reset_index(drop=True)
    diffs = sorted_ts.diff().fillna(expected_delta)
    gaps = sorted_ts[diffs > expected_delta]
    return pd.DataFrame({"timestamp": gaps, "gap": diffs[diffs > expected_delta]})


def run_dqc(
    df: pd.DataFrame,
    time_column: str,
    key_columns: Iterable[str],
    expected_delta: pd.Timedelta,
) -> DqcResult:
    duplicates = check_duplicates(df, key_columns)
    nulls = check_nulls(df)
    gaps = check_gaps(df[time_column], expected_delta)
    summary = {
        "row_count": len(df),
        "duplicate_count": len(duplicates),
        "gap_count": len(gaps),
        "null_counts": nulls.set_index("column")["null_count"].to_dict(),
    }
    return DqcResult(summary=summary, gaps=gaps, duplicates=duplicates, nulls=nulls)