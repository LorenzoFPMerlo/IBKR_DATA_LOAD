from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import json
from pathlib import Path
import pandas as pd
from ibkr_data_load.utils import atomic_write

@dataclass
class DqcResult:
    summary: dict
    gaps: pd.DataFrame
    duplicates: pd.DataFrame
    nulls: pd.DataFrame
    outliers: pd.DataFrame

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

def check_tick_sanity(df: pd.DataFrame) -> pd.DataFrame:
    checks = []
    if "price" in df.columns:
        checks.append(df["price"] > 0)
    if "bid" in df.columns:
        checks.append(df["bid"] > 0)
    if "ask" in df.columns:
        checks.append(df["ask"] > 0)
    if "size" in df.columns:
        checks.append(df["size"] >= 0)
    if "bidSize" in df.columns:
        checks.append(df["bidSize"] >= 0)
    if "askSize" in df.columns:
        checks.append(df["askSize"] >= 0)
    if not checks:
        return pd.DataFrame()
    mask = checks[0]
    for check in checks[1:]:
        mask &= check
    return df.loc[~mask].copy()

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
    return DqcResult(
        summary=summary, gaps=gaps, duplicates=duplicates, nulls=nulls, outliers=pd.DataFrame()
    )


def _ensure_utc(series: pd.Series) -> pd.Series:
    timestamps = pd.to_datetime(series)
    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize("UTC")
    return timestamps.dt.tz_convert("UTC")


def check_ohlc_outliers(df: pd.DataFrame) -> pd.DataFrame:
    high_ok = df["high"] >= df[["open", "close", "low"]].max(axis=1)
    low_ok = df["low"] <= df[["open", "close", "high"]].min(axis=1)
    volume_ok = df["volume"] >= 0
    bar_count_ok = df["barCount"] >= 0
    mask = ~(high_ok & low_ok & volume_ok & bar_count_ok)
    return df.loc[mask].copy()


def run_bars_dqc(
    df: pd.DataFrame,
    time_column: str,
    key_columns: Iterable[str],
    expected_delta: pd.Timedelta,
) -> DqcResult:
    if df.empty:
        return DqcResult(
            summary={"row_count": 0, "duplicate_count": 0, "gap_count": 0, "null_counts": {}},
            gaps=pd.DataFrame(),
            duplicates=pd.DataFrame(),
            nulls=pd.DataFrame(),
            outliers=pd.DataFrame(),
        )
    df = df.copy()
    df[time_column] = _ensure_utc(df[time_column])
    duplicates = check_duplicates(df, key_columns)
    nulls = check_nulls(df)
    gaps = check_gaps(df[time_column], expected_delta)
    outliers = check_ohlc_outliers(df)
    summary = {
        "row_count": len(df),
        "duplicate_count": len(duplicates),
        "gap_count": len(gaps),
        "null_counts": nulls.set_index("column")["null_count"].to_dict(),
        "outlier_count": len(outliers),
        "gap_mode": "delta_only",
    }
    return DqcResult(
        summary=summary, gaps=gaps, duplicates=duplicates, nulls=nulls, outliers=outliers
    )

def run_ticks_dqc(
    df: pd.DataFrame,
    time_column: str,
    key_columns: Iterable[str],
    expected_delta: pd.Timedelta | None = None,
) -> DqcResult:
    if df.empty:
        return DqcResult(
            summary={
                "row_count": 0,
                "duplicate_count": 0,
                "gap_count": 0,
                "null_counts": {},
                "invalid_count": 0,
                "gap_mode": "skipped",
            },
            gaps=pd.DataFrame(),
            duplicates=pd.DataFrame(),
            nulls=pd.DataFrame(),
            outliers=pd.DataFrame(),
        )
    df = df.copy()
    df[time_column] = _ensure_utc(df[time_column])
    duplicates = check_duplicates(df, key_columns)
    nulls = check_nulls(df)
    if expected_delta is not None:
        gaps = check_gaps(df[time_column], expected_delta)
        gap_mode = "delta_only"
    else:
        gaps = pd.DataFrame()
        gap_mode = "skipped"
    invalid = check_tick_sanity(df)
    summary = {
        "row_count": len(df),
        "duplicate_count": len(duplicates),
        "gap_count": len(gaps),
        "null_counts": nulls.set_index("column")["null_count"].to_dict(),
        "invalid_count": len(invalid),
        "gap_mode": gap_mode,
    }
    return DqcResult(
        summary=summary,
        gaps=gaps,
        duplicates=duplicates,
        nulls=nulls,
        outliers=invalid,
    )


def run_fundamentals_dqc(
    df: pd.DataFrame,
    required_columns: Iterable[str],
    key_columns: Iterable[str],
) -> DqcResult:
    missing = [col for col in required_columns if col not in df.columns]
    schema_ok = not missing
    nulls = check_nulls(df) if schema_ok else pd.DataFrame()
    duplicates = check_duplicates(df, key_columns) if schema_ok else pd.DataFrame()
    raw_payload_empty_count = 0
    if schema_ok and "raw_payload" in df.columns:
        raw_payload_empty_count = int(
            df["raw_payload"].fillna("").astype(str).str.len().eq(0).sum()
        )
    key_nulls = {
        col: int(df[col].isna().sum()) for col in key_columns if col in df.columns
    }
    summary = {
        "row_count": len(df),
        "duplicate_count": len(duplicates),
        "null_counts": nulls.set_index("column")["null_count"].to_dict() if not nulls.empty else {},
        "schema_ok": schema_ok,
        "missing_columns": missing,
        "raw_payload_empty_count": raw_payload_empty_count,
        "key_null_counts": key_nulls,
    }
    return DqcResult(
        summary=summary,
        gaps=pd.DataFrame(),
        duplicates=duplicates,
        nulls=nulls,
        outliers=pd.DataFrame(),
    )


def write_ticks_dqc(result: DqcResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(output_dir / "dqc_summary.json", json.dumps(result.summary, indent=2))
    if not result.gaps.empty:
        result.gaps.to_csv(output_dir / "dqc_gaps.csv", index=False)
    if not result.outliers.empty:
        result.outliers.to_csv(output_dir / "dqc_invalid.csv", index=False)
    if not result.duplicates.empty:
        result.duplicates.to_csv(output_dir / "dqc_duplicates.csv", index=False)


def write_bars_dqc(result: DqcResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(output_dir / "dqc_summary.json", json.dumps(result.summary, indent=2))
    result.gaps.to_csv(output_dir / "dqc_gaps.csv", index=False)
    if not result.outliers.empty:
        result.outliers.to_csv(output_dir / "dqc_outliers.csv", index=False)
    if not result.duplicates.empty:
        result.duplicates.to_csv(output_dir / "dqc_duplicates.csv", index=False)


def write_fundamentals_dqc(result: DqcResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(output_dir / "dqc_summary.json", json.dumps(result.summary, indent=2))
    if not result.nulls.empty:
        result.nulls.to_csv(output_dir / "dqc_nulls.csv", index=False)
    if not result.duplicates.empty:
        result.duplicates.to_csv(output_dir / "dqc_duplicates.csv", index=False)