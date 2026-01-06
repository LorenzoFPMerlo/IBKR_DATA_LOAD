from __future__ import annotations

import argparse
import json
from pathlib import Path

from ibkr_data_load.app import LoadApp
from ibkr_data_load.config import load_config
from ibkr_data_load.logging import build_logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="load",
        description="IBKR data loader (IB Gateway/TWS).",
    )
    parser.add_argument("--isin", help="ISIN identifier")
    parser.add_argument("--ticker", help="Ticker/symbol identifier")
    parser.add_argument("--profile", default="PROFILE_A", help="Dataset profile")
    parser.add_argument("--datasets", default="", help="Comma-separated dataset names")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--duration", help="IB duration string")
    parser.add_argument("--bar-size", help="IB bar size string")
    parser.add_argument("--live", action="store_true", help="Enable live subscriptions")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoints")
    parser.add_argument("--dry-run", action="store_true", help="Plan-only; no IB calls")
    parser.add_argument("--execute", action="store_true", help="Run IB requests")
    parser.add_argument("--dqc", default="warn", choices=["warn", "strict"], help="DQC mode")
    parser.add_argument("--output-dir", default="data", help="Output directory")
    parser.add_argument("--gateway", action="store_true", help="Use IB Gateway")
    parser.add_argument("--tws", action="store_true", help="Use TWS")
    parser.add_argument("--config", default="config/defaults.yaml", help="Defaults config")
    parser.add_argument("--user-config", default="config/user.yaml", help="User config override")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    identifier = args.isin or args.ticker
    if not identifier:
        parser.error("Provide --isin or --ticker.")
    config = load_config(Path(args.config), Path(args.user_config))
    logger = build_logger(config.logging)
    dry_run = args.dry_run or not args.execute
    logger.info("Starting LOAD (dry_run=%s)", dry_run)
    app = LoadApp.from_paths(args.config, args.user_config)
    identifier_type = "isin" if args.isin else "ticker"
    plan = app.run_with_options(
        [identifier],
        identifier_type=identifier_type,
        dry_run=dry_run,
        execute=args.execute,
        start_date=args.start_date,
        end_date=args.end_date,
        resume=args.resume,
    )
    if dry_run:
        print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())