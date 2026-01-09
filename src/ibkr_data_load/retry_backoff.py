from __future__ import annotations

from typing import Any

from tenacity import Retrying, stop_after_attempt, wait_exponential

from ibkr_data_load.config import PacingConfig


def build_retryer(
    pacing: PacingConfig,
    logger,
    run_id: str,
    context: dict[str, Any] | None = None,
) -> Retrying:
    def _before_sleep(retry_state) -> None:
        if not logger:
            return
        payload = {
            "run_id": run_id,
            "status": "backoff",
            "attempt": retry_state.attempt_number,
            "max_retries": pacing.max_retries,
            "sleep_seconds": getattr(retry_state.next_action, "sleep", None),
            "context": context or {},
        }
        logger.warning("pacing violation; backing off", extra=payload)

    return Retrying(
        stop=stop_after_attempt(pacing.max_retries),
        wait=wait_exponential(
            multiplier=pacing.base_backoff_seconds,
            min=pacing.base_backoff_seconds,
            max=pacing.max_backoff_seconds,
        ),
        reraise=True,
        before_sleep=_before_sleep,
    )