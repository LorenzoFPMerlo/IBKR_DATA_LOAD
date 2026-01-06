from __future__ import annotations

from dataclasses import dataclass

from ib_insync import ContractDetails, IB


@dataclass(frozen=True)
class MarketDepthResult:
    row_count: int
    dataset_name: str


def run_market_depth_snapshot(
    ib: IB,
    contract_details: ContractDetails,
    num_rows: int,
    duration_seconds: int,
    logger=None,
) -> MarketDepthResult:
    contract = contract_details.contract
    depth = ib.reqMktDepth(contract, num_rows, isSmartDepth=False)
    if logger:
        logger.info(
            "Starting market depth subscription conId=%s rows=%s duration=%s",
            contract.conId,
            num_rows,
            duration_seconds,
        )
    ib.sleep(duration_seconds)
    ib.cancelMktDepth(contract, False)
    depth_rows = getattr(depth, "domBids", []) + getattr(depth, "domAsks", [])
    if logger:
        logger.info(
            "Stopped market depth subscription conId=%s rows=%s",
            contract.conId,
            len(depth_rows),
        )
    return MarketDepthResult(row_count=len(depth_rows), dataset_name="market_depth")