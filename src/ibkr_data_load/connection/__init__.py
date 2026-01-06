from __future__ import annotations
from typing import Any

__all__ = ["IbkrConnector"]


def __getattr__(name: str) -> Any:
    if name == "IbkrConnector":
        from .connector import IbkrConnector

        return IbkrConnector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")