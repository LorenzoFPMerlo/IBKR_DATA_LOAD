from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ib_insync import ContractDetails, IB


@dataclass(frozen=True)
class LiveTickByTickResult:
    tick_count: int
    dataset_name: str


def run_tick_by_tick_trades_live(
    ib: IB,
    contract_details: ContractDetails,
    duration_seconds: int,
    logger=None,
) -> LiveTickByTickResult:
    contract = contract_details.contract
    subscription = ib.reqTickByTickData(contract, "Last", 0, False)
    if logger:
        logger.info(
            "Starting tick-by-tick live subscription conId=%s duration=%s",
            contract.conId,
            duration_seconds,
        )
    ib.sleep(duration_seconds)
    ib.cancelTickByTickData(contract)
    ticks = getattr(subscription, "ticks", []) or []
    if logger:
        logger.info(
            "Stopped tick-by-tick live subscription conId=%s ticks=%s",
            contract.conId,
            len(ticks),
        )
    return LiveTickByTickResult(tick_count=len(ticks), dataset_name="tick_by_tick_trades_live")