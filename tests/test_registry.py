import pytest

from ibkr_data_load.datasets.registry import CAPABILITY_TAGS, STATUS_VALUES, DatasetRegistry


def test_registry_status_and_reason():
    registry = DatasetRegistry.default()
    for dataset in registry.list():
        assert dataset.status in STATUS_VALUES
        if dataset.status != "implemented":
            assert dataset.reason


def test_registry_capability_probe_populated():
    registry = DatasetRegistry.default()
    for dataset in registry.list():
        assert dataset.capability_probe in CAPABILITY_TAGS


def test_registry_contains_expected_names():
    registry = DatasetRegistry.default()
    expected = {
        "contract_details",
        "historical_bars",
        "option_chain_discovery",
        "option_surface_snapshot_daily",
        "live_quote_snapshot",
        "fundamentals_report",
        "historical_ticks_trades",
        "tick_by_tick_trades_live",
    }
    assert expected.issubset(set(registry.datasets.keys()))