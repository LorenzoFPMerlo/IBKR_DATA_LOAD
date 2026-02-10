# Acceptance Tests (Opt-in Live IBKR)

Set `RUN_IBKR_ACCEPTANCE=1` and ensure IB Gateway/TWS is running and authenticated.

## Historical bars
```bash
load --ticker SPY --datasets historical_bars --currency USD --primary-exchange ARCA --start-date 2024-01-01 --end-date 2024-01-15 --execute
```

## Option surface snapshot
```bash
load --ticker SPX --datasets option_surface_snapshot_daily --sec-type IND --exchange CBOE --currency USD --execute
```

## Fundamentals
```bash
load --ticker SPY --datasets fundamentals_report --execute
```

## Option contract historical bars (small bounded run)
```bash
load --ticker SPX --datasets option_contract_historical_bars --sec-type IND --exchange CBOE --currency USD --expirations-next 1 --strikes-nearest 2 --rights both --max-contracts 20 --bar-size "1 min" --duration "2 D" --execute
```
If ambiguity happens, run with `--debug-ib` and inspect `data/<INSTRUMENT_ID>/meta/resolution.json`.

## Expected outputs
- Run summary: `data/meta/runs/<RUN_ID>/run_summary.json`
- Historical bars: `data/<INSTRUMENT_ID>/market/historical/`
- Debug bundle (when `--debug-ib`): `data/meta/runs/<RUN_ID>/debug/`
- Options snapshot: `data/<INSTRUMENT_ID>/derivatives/options/snapshots/YYYY/MM/DD/`
- Option contract history: `data/<INSTRUMENT_ID>/derivatives/options/contracts/...`
- Fundamentals status/data: `data/<INSTRUMENT_ID>/fundamentals/`

## Verification checklist
- `job_result.json` exists under `data/<INSTRUMENT_ID>/meta/execution/<DATASET_ID>/`.
- DQC folders exist under instrument `meta/dqc` (or fundamentals meta/dqc).
- Re-run with overlap and verify no duplicate key rows.
- Re-run with `--resume` after forced interruption and verify continued progress.
