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
    parser.add_argument("--datasets", default="", help="Comma-separated dataset names (e.g., fundamentals_report)")
    parser.add_argument("--options-snapshot", action="store_true", help ="Run option surface snapshot (daily)",)
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--duration", help="IB duration string")
    parser.add_argument("--bar-size", help="IB bar size string")
    parser.add_argument("--currency", help="Instrument resolution currency override (e.g., USD)")
    parser.add_argument("--primary-exchange", help="Instrument resolution primary exchange override (e.g., ARCA)")
    parser.add_argument("--sec-type", help="Instrument resolution secType override (e.g., STK, IND)")
    parser.add_argument("--exchange", help="Instrument resolution exchange override (e.g., SMART, CBOE)")
    parser.add_argument("--live", action="store_true", help="Enable live subscriptions")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoints in data/.../meta/checkpoints")
    parser.add_argument("--debug-ib", action="store_true", help="Enable verbose IB diagnostics bundle output")
    parser.add_argument("--debug", action="store_true", help="Alias for --debug-ib")
    parser.add_argument("--fundamentals-report-type", help="Override fundamentals report type (e.g., ReportsFinStatements)")
    parser.add_argument("--expirations-next", type=int, help="Option historical bars: use next N expirations")
    parser.add_argument("--expiration", action="append", default=[], help="Option historical bars: expiration YYYY-MM-DD (repeatable or comma-separated)")
    parser.add_argument("--strikes", help="Option historical bars: comma-separated strikes")
    parser.add_argument("--strikes-nearest", type=int, help="Option historical bars: nearest K strikes to reference")
    parser.add_argument("--rights", choices=["C", "P", "both"], default="both", help="Option historical bars: contract rights")
    parser.add_argument("--max-contracts", type=int, help="Option historical bars: safety cap")
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
    expiration_values: list[str] = []
    for raw in args.expiration:
        expiration_values.extend([item.strip() for item in raw.split(",") if item.strip()])

    strike_values: list[float] | None = None
    if args.strikes:
        strike_values = [float(value.strip()) for value in args.strikes.split(",") if value.strip()]
    
    plan = app.run_with_options(
        [identifier],
        identifier_type=identifier_type,
        dry_run=dry_run,
        execute=args.execute,
        start_date=args.start_date,
        end_date=args.end_date,
        resume=args.resume,
        datasets=[value.strip() for value in args.datasets.split(",") if value.strip()],
        options_snapshot=args.options_snapshot,
        fundamentals_report_type=args.fundamentals_report_type,
        debug_ib=args.debug_ib or args.debug,
        option_expirations_next=args.expirations_next,
        option_expirations=expiration_values,
        option_strikes=strike_values,
        option_strikes_nearest=args.strikes_nearest,
        option_rights=args.rights,
        option_max_contracts=args.max_contracts,
        option_bar_size=args.bar_size,
        option_duration=args.duration,
        resolution_currency=args.currency,
        resolution_primary_exchange=args.primary_exchange,
        resolution_sec_type=args.sec_type,
        resolution_exchange=args.exchange,
    )
    if dry_run:
        print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())