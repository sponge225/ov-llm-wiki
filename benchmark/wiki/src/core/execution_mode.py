"""Execution modes for the Wiki benchmark."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BASELINE_MODE = "baseline"
VIKINGBOT_MODE = "vikingbot"
SUPPORTED_EXECUTION_MODES = frozenset({BASELINE_MODE, VIKINGBOT_MODE})


def resolve_execution_mode(config: Mapping[str, Any]) -> str:
    """Return the configured execution mode.

    Missing mode keeps the existing baseline behavior for old configs.
    """
    execution = config.get("execution", {}) or {}
    mode = str(execution.get("mode", BASELINE_MODE) or BASELINE_MODE).strip()
    if mode not in SUPPORTED_EXECUTION_MODES:
        supported = ", ".join(sorted(SUPPORTED_EXECUTION_MODES))
        raise ValueError(f"Unsupported execution.mode {mode!r}; supported modes: {supported}")
    return mode
