from datetime import datetime, timezone

import pandas as pd

from ib_insync import Option

from ibkr_data_load.config import OptionsSnapshotConfig
from ibkr_data_load.options.discovery import OptionChainEntry, OptionChainMetadata
from ibkr_data_load.options.surface_snapshot import build_snapshot_plan, update_contract_registry


def test_option_chain_metadata_roundtrip():
    metadata = OptionChainMetadata(
        underlying_conId=123,
        symbol="ABC",
        secType="STK",
        currency="USD",
        asof_utc="2024-01-01T00:00:00Z",
        entries=[
            OptionChainEntry(
                trading_class="ABC",
                exchange="SMART",
                expirations=["20240119", "20240216"],
                strikes=[90.0, 100.0, 110.0],
                multiplier="100",
            )
        ],
    )
    payload = metadata.to_dict()
    restored = OptionChainMetadata.from_dict(payload)
    assert restored == metadata
    assert restored.all_expirations() == ["20240119", "20240216"]
    assert restored.all_strikes() == [90.0, 100.0, 110.0]


def test_snapshot_plan_counts():
    metadata = OptionChainMetadata(
        underlying_conId=123,
        symbol="ABC",
        secType="STK",
        currency="USD",
        asof_utc="2024-01-01T00:00:00Z",
        entries=[
            OptionChainEntry(
                trading_class="ABC",
                exchange="SMART",
                expirations=["20240119", "20240216", "20240315"],
                strikes=[80.0, 90.0, 100.0, 110.0, 120.0],
                multiplier="100",
            )
        ],
    )
    config = OptionsSnapshotConfig(
        enabled=True,
        max_contracts_per_batch=5,
        sleep_between_batches_seconds=1.0,
        snapshot_wait_seconds=1.0,
        max_expirations=2,
        strike_band_pct=0.1,
        strike_step=1,
        exchange="SMART",
    )
    plan = build_snapshot_plan(metadata, config, underlying_price=100.0)
    assert plan["expiration_count"] == 2
    assert plan["strike_count"] == 3
    assert plan["estimated_contracts"] == 12
    assert plan["estimated_batches"] == 3


def test_contract_registry_update_marks_expired():
    snapshot_time = datetime(2024, 1, 10, tzinfo=timezone.utc)
    existing = pd.DataFrame(
        [
            {
                "underlying_conId": 123,
                "option_conId": 1,
                "expiration": "20240105",
                "strike": 100.0,
                "right": "C",
                "tradingClass": "ABC",
                "exchange": "SMART",
                "currency": "USD",
                "multiplier": "100",
                "status": "active",
                "first_seen": "2024-01-01T00:00:00Z",
                "last_seen": "2024-01-02T00:00:00Z",
            }
        ]
    )
    contract = Option("ABC", "20240105", 100.0, "C", "SMART")
    contract.conId = 1
    updated = update_contract_registry(existing, [contract], 123, snapshot_time)
    assert updated.loc[0, "status"] == "expired"
    assert updated.loc[0, "last_seen"] == snapshot_time.isoformat()