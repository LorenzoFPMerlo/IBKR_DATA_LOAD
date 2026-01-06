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


@dataclass
class AppConfig:
    output_dir: str
    logging: LoggingConfig
    connection: ConnectionConfig


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
    }
    for env_key, path in env_map.items():
        full_key = f"LOAD_{env_key}"
        if full_key not in os.environ:
            continue
        value: Any = os.environ[full_key]
        if path[-1] in {"port", "client_id"}:
            value = int(value)
        if path[-1] == "json_logs":
            value = value.lower() in {"1", "true", "yes", "on"}
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
        ),
    )