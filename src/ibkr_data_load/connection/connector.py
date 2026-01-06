from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from ib_insync import IB
from tenacity import Retrying, RetryError, stop_after_attempt, wait_exponential

from ibkr_data_load.config import AppConfig, load_config
from ibkr_data_load.logging import build_logger


class IbkrConnectionError(RuntimeError):
    pass


@dataclass
class _ConnectionContext:
    connector: "IbkrConnector"

    def __enter__(self) -> IB:
        self.connector.connect_with_retry()
        return self.connector.ib

    def __exit__(self, exc_type, exc, tb) -> None:
        self.connector.disconnect()


class IbkrConnector:
    def __init__(self, config: AppConfig, run_id: str, logger=None) -> None:
        self.config = config
        self.run_id = run_id
        self.logger = logger or build_logger(config.logging)
        self.ib = IB()

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(self)

    def connect_with_retry(self) -> IB:
        retryer = Retrying(
            stop=stop_after_attempt(self.config.connection.max_retries),
            wait=wait_exponential(
                multiplier=self.config.connection.retry_backoff_seconds,
                min=self.config.connection.retry_backoff_seconds,
                max=30,
            ),
            reraise=True,
        )
        try:
            for attempt in retryer:
                with attempt:
                    self.logger.info(
                        "run_id=%s connecting to IB host=%s port=%s client_id=%s",
                        self.run_id,
                        self.config.connection.host,
                        self.config.connection.port,
                        self.config.connection.client_id,
                    )
                    self.ib.connect(
                        self.config.connection.host,
                        self.config.connection.port,
                        clientId=self.config.connection.client_id,
                        timeout=self.config.connection.connect_timeout_seconds,
                    )
                    if not self.ib.isConnected():
                        raise IbkrConnectionError("IB connection reported disconnected")
        except RetryError as exc:
            raise IbkrConnectionError("Failed to connect to IBKR after retries") from exc
        return self.ib

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()
            self.logger.info("run_id=%s disconnected from IB", self.run_id)


def main() -> int:
    """Connection smoke test.

    Usage: python -m ibkr_data_load.connection.connector
    """
    parser = argparse.ArgumentParser(description="Test IBKR connection and disconnect.")
    parser.add_argument("--config", default="config/defaults.yaml", help="Defaults config")
    parser.add_argument("--user-config", default="config/user.yaml", help="User config")
    args = parser.parse_args()
    config = load_config(Path(args.config), Path(args.user_config))
    logger = build_logger(config.logging)
    connector = IbkrConnector(config=config, run_id="connection_test", logger=logger)
    with connector.connect():
        logger.info("run_id=%s connection test succeeded", connector.run_id)
        print(
            f"✅ Connected to IBKR at {config.connection.host}:{config.connection.port} "
            f"(client_id={config.connection.client_id})"
        )
    print("✅ Disconnected from IBKR")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())