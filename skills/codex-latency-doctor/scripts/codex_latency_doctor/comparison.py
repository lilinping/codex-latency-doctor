"""Compare the allowlisted evidence in two saved audit reports."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


# Labels include units so the same list drives both the comparison and the table.
METRICS = {
    "model_header_median": "Step to HTTP headers median (s)",
    "model_header_max": "Step to HTTP headers max (s)",
    "model_header_at_least_30s": "Paired header waits >= 30s",
    "model_header_at_least_120s": "Paired header waits >= 120s",
    "compaction_requests": "Recorded automatic compaction requests",
    "mid_turn_compaction_requests": "Recorded mid-turn compaction requests",
    "compaction_median": "Paired compaction median (s)",
    "compaction_max": "Paired compaction max (s)",
    "compaction_http_errors": "Compaction HTTP error log rows",
    "model_retries": "Model retry log rows",
    "stream_transport_retries": "Response transport retry log rows",
    "model_http_errors": "Model HTTP error log rows",
    "model_stream_errors": "Model stream error log rows",
    "tool_median": "Completed tool call median (ms)",
}


class ComparisonError(ValueError):
    """A safe-to-display snapshot error."""


def _snapshot(path: Path, name: str) -> dict:
    limit = 4 * 1024 * 1024
    try:
        if not path.is_file():
            raise ComparisonError(f"{name} must be a readable audit JSON file.")
        with path.open("rb") as file:
            contents = file.read(limit + 1)
    except OSError:
        raise ComparisonError(f"Could not read the {name.lower()} snapshot.") from None
    if len(contents) > limit:
        raise ComparisonError(f"{name} snapshot exceeds 4 MiB.")
    try:
        report = json.loads(contents)
    except (ValueError, RecursionError):
        raise ComparisonError(f"{name} snapshot is not valid JSON.") from None
    if (
        not isinstance(report, dict)
        or type(report.get("window_hours")) is not int
        or not 1 <= report["window_hours"] <= 168
        or not all(isinstance(report.get(section), dict) for section in ("config", "logs", "power"))
        or report["logs"].get("status") not in ("ok", "missing", "unsupported_schema", "unavailable")
    ):
        raise ComparisonError(f"{name} must come from --json audit.")
    return report


def _count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _capture_time(report: dict) -> datetime | None:
    value = report.get("captured_at_utc")
    if isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is not None:
                return timestamp.astimezone(UTC)
        except (ValueError, OverflowError):
            pass
    return None


def _coverage(report: dict, capture_time: datetime | None) -> dict:
    logs = report["logs"]
    thresholds = logs.get("effective_threshold_observations")
    return {
        "captured_at_utc": capture_time.isoformat() if capture_time is not None else None,
        "window_hours": report["window_hours"],
        "log_status": logs["status"],
        "rows_scanned": _count(logs.get("rows_scanned")),
        "oldest_row_age_seconds": _count(logs.get("oldest_row_age_seconds")),
        "truncated": logs.get("truncated") if type(logs.get("truncated")) is bool else None,
        "configured_compact_tokens": _count(report["config"].get("compact_tokens")),
        "observed_compact_tokens": sorted(
            int(key) for key in thresholds if key.isascii() and key.isdigit() and 1 <= len(key) <= 12
        ) if isinstance(thresholds, dict) else [],
    }


def _measurements(report: dict, coverage: dict) -> dict:
    if coverage["log_status"] != "ok" or not coverage["rows_scanned"]:
        return {}
    logs = report["logs"]

    def group(name):
        value = logs.get(name)
        return value if isinstance(value, dict) else {}

    def duration(group: dict, key: str) -> tuple[int | float | None, int | None]:
        samples = _count(group.get("samples"))
        number = group.get(key)
        # Audit timing fields are nonnegative and well within this bound.
        valid = type(number) in (int, float) and 0 <= number <= 10**18
        return number if valid and samples else None, samples

    headers = group("model_step_to_http_headers_seconds")
    header_samples = _count(headers.get("samples"))
    requests = group("compaction_requests")
    phases = [_count(requests.get(phase)) for phase in ("pre_turn", "mid_turn", "other")]
    compaction_time = group("compaction_log_elapsed_seconds")
    return {
        "model_header_median": duration(headers, "median"),
        "model_header_max": duration(headers, "max"),
        "model_header_at_least_30s": (_count(headers.get("at_least_30_seconds")) if header_samples else None, header_samples),
        "model_header_at_least_120s": (_count(headers.get("at_least_120_seconds")) if header_samples else None, header_samples),
        "compaction_requests": (sum(phases) if None not in phases else None, None),
        "mid_turn_compaction_requests": (_count(requests.get("mid_turn")), None),
        "compaction_median": duration(compaction_time, "median"),
        "compaction_max": duration(compaction_time, "max"),
        "compaction_http_errors": (_count(group("compaction_http_response_rows").get("errors")), None),
        "model_retries": (_count(logs.get("model_retry_rows")), None),
        "stream_transport_retries": (_count(group("model_retry_rows_by_reason").get("stream_transport")), None),
        "model_http_errors": (_count(logs.get("model_http_error_rows")), None),
        "model_stream_errors": (_count(logs.get("model_stream_error_rows")), None),
        "tool_median": duration(group("tool_call_elapsed_ms"), "median"),
    }


def compare_audits(baseline: Path, current: Path) -> dict:
    before_report = _snapshot(baseline, "Baseline")
    after_report = _snapshot(current, "Current")
    before_time = _capture_time(before_report)
    after_time = _capture_time(after_report)
    before = _coverage(before_report, before_time)
    after = _coverage(after_report, after_time)

    reasons = []
    notes = []
    if before["window_hours"] != after["window_hours"]:
        reasons.append("The audit windows have different lengths.")
    if not before_time or not after_time:
        notes.append("A capture time is unavailable. Check that the audit windows do not overlap.")
    elif after_time - before_time <= timedelta(hours=after["window_hours"]):
        reasons.append("The audit windows overlap or the captures are in reverse order.")
    if any(side["log_status"] != "ok" or not side["rows_scanned"] for side in (before, after)):
        reasons.append("At least one snapshot has no readable log rows in its window.")
    if any(side["truncated"] is True for side in (before, after)):
        reasons.append("At least one log scan was truncated.")

    baseline_metrics = _measurements(before_report, before)
    current_metrics = _measurements(after_report, after)
    metrics = {}
    for key in METRICS:
        old, old_samples = baseline_metrics.get(key, (None, None))
        new, new_samples = current_metrics.get(key, (None, None))
        metrics[key] = {
            "before": old,
            "after": new,
            "change": round(new - old, 2) if not reasons and old is not None and new is not None else None,
            "before_samples": old_samples,
            "after_samples": new_samples,
        }

    return {
        "status": "ok",
        "baseline": before,
        "current": after,
        "deltas_available": not reasons,
        "delta_limitations": reasons,
        "notes": notes,
        "metrics": metrics,
    }
