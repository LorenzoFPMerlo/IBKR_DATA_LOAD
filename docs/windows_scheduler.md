# Windows Task Scheduler (Daily LOAD Runs)

## Overview
You can schedule the LOAD CLI to run daily on Windows 11 using Task Scheduler. This is useful for recurring jobs such as `option_surface_snapshot_daily` updates. LOAD writes idempotent, deduplicated output, so re-running daily is safe, and `--resume` can continue after an interruption.

## Step-by-step setup
1) Open **Task Scheduler** (Start Menu → search “Task Scheduler”).
2) Click **Create Basic Task** and name it (e.g., “IBKR LOAD Daily”).
3) **Trigger**: Daily at a fixed time (e.g., 07:00).
4) **Action**: Start a program.
   - **Program/script**:
     ```
     C:\Users\<USER>\miniconda3\envs\ibload\python.exe
     ```
   - **Arguments**:
     ```
     -m ibkr_data_load.cli --ticker SPX --datasets option_surface_snapshot_daily --execute
     ```
   - **Start in**:
     ```
     C:\path\to\IBKR_DATA_LOAD
     ```
5) **Environment variables** (optional):
   - If you use per-user configuration, set `LOAD_CONFIG` or `LOAD_USER_CONFIG` at the OS level, or use Task Scheduler’s “Start in” to ensure config paths resolve.
   - If you want integration tests to remain off, set:
     ```
     RUN_IBKR_TESTS=0
     ```
6) **Redirect output/logs** (optional):
   - Use PowerShell to capture logs:
     ```
     -m ibkr_data_load.cli --ticker SPX --datasets option_surface_snapshot_daily --execute *> C:\logs\ibkr_load.log 2>&1
     ```

## Suggested patterns
- **Daily options surface snapshot**: use a watchlist in `config/user.yaml` and call `load` with `--datasets option_surface_snapshot_daily`.
- **Config-driven instruments**: keep instruments/datasets in config files to avoid hardcoding in the Task Scheduler arguments.

## Resume & idempotency
- Use `--resume` after a failed run to continue from the last checkpoint.
- The loader writes checkpoints under `data/.../meta/checkpoints` and uses idempotent deduplication for appends, so daily runs can safely continue even after interruptions.