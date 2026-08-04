#!/usr/bin/env python3
"""Extract and summarize TTS_LATENCY_SUMMARY records from server logs."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

PREFIX = "TTS_LATENCY_SUMMARY "


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("logfile", type=Path)
    args = parser.parse_args()
    records = []
    for line in args.logfile.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = line.find(PREFIX)
        if marker < 0:
            continue
        raw = line[marker + len(PREFIX) :].strip()
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    if not records:
        print(json.dumps({"status": "no_records", "count": 0}, ensure_ascii=False))
        return 2

    metric_names = sorted(
        {
            key
            for record in records
            for key, value in record.items()
            if key.endswith("_ms") and isinstance(value, (int, float))
        }
    )
    metrics = {}
    for name in metric_names:
        values = [float(record[name]) for record in records if isinstance(record.get(name), (int, float))]
        metrics[name] = {
            "count": len(values),
            "p50": round(statistics.median(values), 3) if values else None,
            "p95": _percentile(values, 0.95),
            "min": round(min(values), 3) if values else None,
            "max": round(max(values), 3) if values else None,
        }
    print(
        json.dumps(
            {"status": "ok", "turn_count": len(records), "metrics": metrics},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
