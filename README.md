# IBKR DATA LOADER

## Purpose
Single-ended application (named "LOAD") for extracting any existing available data for any given IBKR covered financial instrument through API requests to TWS and IB Gateway. LOAD is a Python 3.11+ CLI and library for planning and executing IBKR data extraction workflows.

## Data source
Access to IBKR real-time and historical market and financial data depends on account's "Market data subscription" from Interactive Brokers (IBKR). Pricing and data bundle subscription availability go to: https://www.interactivebrokers.ca/en/accounts/fees/marketData.php?ib_entity=ca 

## API Reference
Reference for all of the TWS API classes go to:
https://ibkrcampus.com/campus/ibkr-api-page/twsapi-ref/#acctsumtags-ref

## Scope
The application must load, fetch, extract any possible data available on a given financial instrument identified by three tokens and inputed as one of the two following tokens: ISIN or ticker. The application must ensure that we identify every existing data entry point linked to the financial instrument and look at the fields of data entry points for the derivatives instrument linked to the financial instrument as well with a different ticker and ISIN but with the underlying asset link (e.g. for stock and ETFs, go for instance into a class named option_data_loader and fetch all available and existing data (e.g. option chains, option contracts open interest/volume). The scope is only the raw data and no computation of financial metrics is to be done in this application/package/program. Why is this? Here we look at what financial data IBKR can recover for us and make the scope within the IBKR API data coverage at the maximum capacity of any user with full subscription. For computing financial metrics and ratios, we will conceive another application/package/program dedicated for it sourcing the data from this very application of IBKR data loader.

## Usage

### API Connection Test
```bash
python -m ibkr_data_load.connection.connector
```

### CLI (plan-only)
```bash
load --ticker SPX --dry-run
```
- Dry-run outputs only a job plan and does not call IBKR APIs.
- No synthetic market data is produced.

### Fundamentals (dry-run + execute)
For ticker:
```bash
load --ticker AMD --datasets fundamentals_report --dry-run
load --ticker AMD --datasets fundamentals_report --execute
```
For ISIN:
```bash
load --isin US0079031078 --datasets fundamentals_report --dry-run
load --isin US0079031078 --datasets fundamentals_report --execute
```

### CLI options
--isin                          ISIN identifier
--ticker                        Ticker/symbol identifier
--profile                       Dataset profile (default: PROFILE_A)
--datasets                      Comma-separated dataset names
--options-snapshot              Run option surface snapshot (daily)
--start-date                    Start date (YYYY-MM-DD)
--end-date                      End date (YYYY-MM-DD)
--duration                      IB duration string
--bar-size                      IB bar size string
--live                          Enable live subscriptions
--resume                        Resume from checkpoints
--fundamentals-report-type      Override fundamentals report type
--dry-run                       Plan-only; no IB calls
--execute                       Run IB requests
--dqc                           DQC mode (warn/strict)
--output-dir                    Output directory
--gateway                       Use IB Gateway
--tws                           Use TWS
--config                        Defaults config path
--user-config                   User config override path

### Library
```python
from ibkr_data_load import LOAD
app = LOAD.from_paths("config/defaults.yaml", "config/user.yaml")
plan = app.run(["AAPL"], dry_run=True)
```

### Scheduling (Windows Task Scheduler)
See `docs/windows_scheduler.md` for a daily Task Scheduler recipe, including options surface snapshots and log redirection.

### API Request Pacing
Backoff for IBKR pacing is configuration-driven in `config/defaults.yaml`:
```yaml
pacing:
  min_interval_seconds: 1.0
  base_backoff_seconds: 2.0
  max_backoff_seconds: 60.0
  max_retries: 6

### Option contract historical bars
```bash
load --ticker SPX --datasets option_contract_historical_bars --expirations-next 3 --strikes-nearest 10 --rights both --max-contracts 100 --bar-size "1 min" --duration "10 D" --execute
```

### Debug IB diagnostics bundle
```bash
load --ticker SPX --datasets historical_bars --execute --debug-ib
```
Creates `data/meta/runs/<RUN_ID>/debug/` with request and error diagnostics.

### Configuration
Override values in `config/defaults.yaml`:
Port For IB Gateway 4001 and TWS 7497
```yaml
connection:
  host: 127.0.0.1
  port: 4001
  client_id: 1
```

### Outputs
1) Instrument resolution metadata

    Contract details

        Path: data/<INSTRUMENT_ID>/meta/contract_details.json

        Purpose: selected contract snapshot.

    Resolution metadata

        Path: data/<INSTRUMENT_ID>/meta/resolution.json

        Purpose: identifier, candidates, selection metadata.

2) Historical bars (historical_bars)

    Data CSV

        Path: data/<INSTRUMENT_ID>/market/historical/historical_bars__<DATASET_ID>.csv

        Naming: historical_bars__{dataset_id}.csv

        Generated by: HistoricalBarsExecutor merge/write path.

    Checkpoint

        Path: data/<INSTRUMENT_ID>/meta/checkpoints/<DATASET_ID>.json

        Purpose: resume watermark.

    DQC outputs

        Path: data/<INSTRUMENT_ID>/meta/dqc/<DATASET_ID>/

        Files:

            dqc_summary.json

            dqc_gaps.csv

            dqc_outliers.csv (if any)

            dqc_duplicates.csv (if any)

        Generated by: write_bars_dqc.

3) Historical ticks (historical_ticks_trades / historical_ticks_bid_ask)

    Data CSV

        Path: data/<INSTRUMENT_ID>/market/historical/<DATASET_NAME>__<DATASET_ID>.csv

        Naming: {dataset_name}__{dataset_id}.csv

        Generated by: HistoricalTicksExecutor merge/write path.

    Checkpoint

        Path: data/<INSTRUMENT_ID>/meta/checkpoints/<DATASET_ID>.json

        Purpose: resume watermark.

    DQC outputs

        Path: data/<INSTRUMENT_ID>/meta/dqc/<DATASET_ID>/

        Files:

            dqc_summary.json

            dqc_gaps.csv (only if gap checks enabled)

            dqc_invalid.csv (if invalid ticks found)

            dqc_duplicates.csv (if duplicates found)

        Generated by: write_ticks_dqc.

4) Option chain discovery metadata

    Option chain metadata JSON

        Path: data/<INSTRUMENT_ID>/derivatives/options/meta/option_chain_discovery.json

        Purpose: cached option chain discovery output.

        Generated by: discover_option_chain.

5) Option surface snapshot (option_surface_snapshot_daily)

    Snapshot data CSV

        Path: data/<INSTRUMENT_ID>/derivatives/options/snapshots/<YYYY>/<MM>/<DD>/option_surface_snapshot_daily__<DATASET_ID>.csv

        Naming: daily partition + dataset id.

        Generated by: run_option_surface_snapshot.

    Contract registry CSV

        Path: data/<INSTRUMENT_ID>/derivatives/options/meta/contract_registry.csv

        Purpose: tracks seen option contracts over time.

        Generated by: update_contract_registry.

    Checkpoint

        Path: data/<INSTRUMENT_ID>/meta/checkpoints/<DATASET_ID>.json

        Purpose: resume batch index.

        Generated by: snapshot runner.

6) Fundamentals (fundamentals_report)

    Fundamentals CSV

        Path: data/<INSTRUMENT_ID>/fundamentals/fundamentals_report__<DATASET_ID>.csv

        Naming: fundamentals_report__{dataset_id}.csv

        Generated by: fetch_fundamentals.

    Entitlement / no data status

        Path: data/<INSTRUMENT_ID>/fundamentals/meta/fundamentals_report_status.json

        Written when: IBKR returns no payload (entitlement/data issue).

    DQC outputs

        Path: data/<INSTRUMENT_ID>/fundamentals/meta/dqc/<DATASET_ID>/

        Files:

            dqc_summary.json

            dqc_nulls.csv (if any nulls)

            dqc_duplicates.csv (if duplicates)

        Generated by: write_fundamentals_dqc.

7) Run summaries (per execution)

    Run summary JSON

        Path: data/meta/runs/<RUN_ID>/run_summary.json

        Purpose: per-run summary with dataset status.

        Generated by: LoadApp.run_with_options
