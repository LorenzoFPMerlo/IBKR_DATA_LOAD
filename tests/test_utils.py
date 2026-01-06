from pathlib import Path

from ibkr_data_load.utils import atomic_write, dataset_id


def test_dataset_id_deterministic():
    first = dataset_id(
        con_id="123",
        dataset_name="historical_bars",
        parameters={"barSize": "1 day"},
        data_source="ibkr",
        timezone="UTC",
        schema_version="1",
    )
    second = dataset_id(
        con_id="123",
        dataset_name="historical_bars",
        parameters={"barSize": "1 day"},
        data_source="ibkr",
        timezone="UTC",
        schema_version="1",
    )
    assert first == second


def test_atomic_write(tmp_path: Path):
    target = tmp_path / "data.json"
    atomic_write(target, "payload")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "payload"