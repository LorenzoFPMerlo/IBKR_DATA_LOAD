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
        "option_chain_snapshot",
        "live_quote_snapshot",
        "fundamentals_report",
    }
    assert expected.issubset(set(registry.datasets.keys()))