from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml


@dataclass
class LoggingConfig:
    level: str
    json_logs: bool


@dataclass
class ConnectionConfig:
    host: str
    port: int
    client_id: int
    connect_timeout_seconds: int
    max_retries: int
    retry_backoff_seconds: float


@dataclass
class InstrumentConfig:
    preferred_exchange: str | None
    currency: str | None
    security_types: list[str]


@dataclass
class HistoricalBarsConfig:
    enabled: bool
    bar_size: str
    what_to_show: str
    use_rth: int
    start_date: str | None
    end_date: str | None
    chunk_duration: str
    overlap_bars: int
    min_request_interval_seconds: float
    max_retries: int
    format_date: int

@dataclass
class AppConfig:
    output_dir: str
    logging: LoggingConfig
    connection: ConnectionConfig
    instruments: InstrumentConfig
    historical_bars: HistoricalBarsConfig

def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_env(merged: dict[str, Any]) -> dict[str, Any]:
    env_map = {
        "OUTPUT_DIR": ("output_dir",),
        "LOG_LEVEL": ("logging", "level"),
        "LOG_JSON": ("logging", "json_logs"),
        "CONNECTION_HOST": ("connection", "host"),
        "CONNECTION_PORT": ("connection", "port"),
        "CONNECTION_CLIENT_ID": ("connection", "client_id"),
        "CONNECTION_TIMEOUT": ("connection", "connect_timeout_seconds"),
        "CONNECTION_MAX_RETRIES": ("connection", "max_retries"),
        "CONNECTION_RETRY_BACKOFF": ("connection", "retry_backoff_seconds"),
    }
    for env_key, path in env_map.items():
        full_key = f"LOAD_{env_key}"
        if full_key not in os.environ:
            continue
        value: Any = os.environ[full_key]
        if path[-1] in {"port", "client_id", "connect_timeout_seconds", "max_retries"}:
            value = int(value)
        if path[-1] == "json_logs":
            value = value.lower() in {"1", "true", "yes", "on"}
        if path[-1] == "retry_backoff_seconds":
            value = float(value)
        target = merged
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = value
    return merged


def load_config(defaults_path: Path, user_path: Path) -> AppConfig:
    defaults = _load_yaml(defaults_path)
    overrides = _load_yaml(user_path)
    merged = _merge(defaults, overrides)
    merged = _apply_env(merged)

    logging_cfg = merged.get("logging", {})
    conn_cfg = merged.get("connection", {})
    instruments_cfg = merged.get("instruments", {})
    historical_cfg = merged.get("historical_bars", {})
    return AppConfig(
        output_dir=merged.get("output_dir", "data"),
        logging=LoggingConfig(
            level=logging_cfg.get("level", "INFO"),
            json_logs=logging_cfg.get("json_logs", True),
        ),
        connection=ConnectionConfig(
            host=conn_cfg.get("host", "127.0.0.1"),
            port=int(conn_cfg.get("port", 4001)),
            client_id=int(conn_cfg.get("client_id", 1)),
            connect_timeout_seconds=int(conn_cfg.get("connect_timeout_seconds", 10)),
            max_retries=int(conn_cfg.get("max_retries", 5)),
            retry_backoff_seconds=float(conn_cfg.get("retry_backoff_seconds", 1)),
        ),
        instruments=InstrumentConfig(
            preferred_exchange=instruments_cfg.get("preferred_exchange"),
            currency=instruments_cfg.get("currency"),
            security_types=list(
                instruments_cfg.get(
                    "security_types",
                    ["stock", "index", "etf", "future", "option", "bond"],
                )
            ),
        ),
        historical_bars=HistoricalBarsConfig(
            enabled=bool(historical_cfg.get("enabled", True)),
            bar_size=historical_cfg.get("bar_size", "1 day"),
            what_to_show=historical_cfg.get("what_to_show", "TRADES"),
            use_rth=int(historical_cfg.get("use_rth", 1)),
            start_date=historical_cfg.get("start_date"),
            end_date=historical_cfg.get("end_date"),
            chunk_duration=historical_cfg.get("chunk_duration", "1 Y"),
            overlap_bars=int(historical_cfg.get("overlap_bars", 5)),
            min_request_interval_seconds=float(
                historical_cfg.get("min_request_interval_seconds", 1.0)
            ),
            max_retries=int(historical_cfg.get("max_retries", 6)),
            format_date=int(historical_cfg.get("format_date", 1)),
        ),
    )