"""IBKR data loader package."""

from typing import Any

__all__ = ["LOAD"]


def __getattr__(name: str) -> Any:
    if name == "LOAD":
        from ibkr_data_load.app import LoadApp as LOAD

        return LOAD
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")