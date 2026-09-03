#!/usr/bin/env python3
"""Repair query token averages in wiki benchmark metric reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _token_value(token_usage: dict[str, Any], primary_key: str, fallback_key: str) -> int:
    primary_value = token_usage.get(primary_key)
    if primary_value not in (None, ""):
        value = int(primary_value or 0)
        if value:
            return value
    return int(token_usage.get(fallback_key, 0) or 0)


def _resolve_output_dir(path: Path) -> Path:
    if path.name == "benchmark_metrics_report.json":
        return path.parent
    return path


def repair_output_dir(output_dir: Path) -> dict[str, Any]:
    output_dir = _resolve_output_dir(output_dir)
    generated_path = output_dir / "generated_answers.json"
    report_path = output_dir / "benchmark_metrics_report.json"

    generated = _read_json(generated_path)
    report = _read_json(report_path)
    results = generated.get("results", [])
    if not isinstance(results, list):
        raise ValueError(f"{generated_path} field 'results' must be a list")

    successful = [item for item in results if item.get("generation_failed") is not True]
    successful_count = len(successful)
    input_total = sum(
        _token_value(item.get("token_usage") or {}, "total_input_tokens", "prompt_tokens")
        for item in successful
    )
    output_total = sum(
        _token_value(item.get("token_usage") or {}, "llm_output_tokens", "completion_tokens")
        for item in successful
    )

    query_efficiency = report.setdefault("Query Efficiency (Average Per Query)", {})
    query_efficiency["Average Input Tokens"] = (
        input_total / successful_count if successful_count else 0
    )
    query_efficiency["Average Output Tokens"] = (
        output_total / successful_count if successful_count else 0
    )
    _write_json(report_path, report)

    return {
        "report": str(report_path),
        "successful_queries": successful_count,
        "total_input_tokens": input_total,
        "total_output_tokens": output_total,
        "average_input_tokens": query_efficiency["Average Input Tokens"],
        "average_output_tokens": query_efficiency["Average Output Tokens"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute wiki QA token averages in benchmark_metrics_report.json "
            "from generated_answers.json."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Wiki output directories or benchmark_metrics_report.json files.",
    )
    args = parser.parse_args()

    for path in args.paths:
        summary = repair_output_dir(path)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
