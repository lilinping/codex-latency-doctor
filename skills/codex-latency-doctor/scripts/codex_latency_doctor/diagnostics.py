"""Bounded, read-only evidence collection from Codex and macOS logs."""

import json
import re
import sqlite3
import subprocess
import sys
import time
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median


ROW_LIMIT = 50_000
BODY_LIMIT = 16_384
QUERY_TIMEOUT = 2.0
PMSET_TIMEOUT = 4.0
WAKE_GRACE_SECONDS = 30
MAX_COMPACTION_SECONDS = 30 * 60
MAX_MODEL_WAIT_SECONDS = 60 * 60

TURN_TARGET = "codex_core::session::turn"
COMPACTION_START_TARGET = "codex_core::session::world_state"
COMPACTION_END_TARGET = "codex_core::history_item_count"
RETRY_TARGET = "codex_core::responses_retry"
HTTP_TARGET = "codex_http_client::client"
TOOL_TARGET = "codex_core::tools::parallel"
STREAM_TARGETS = {
    "codex_api::sse::responses",
    "codex_api::endpoint::responses_websocket",
    "codex_core::stream_events_utils",
}
BODY_TARGETS = {
    TURN_TARGET, COMPACTION_START_TARGET, COMPACTION_END_TARGET, RETRY_TARGET,
    HTTP_TARGET, TOOL_TARGET, "feedback_tags", *STREAM_TARGETS,
}
TASK_TARGETS = {TURN_TARGET, "feedback_tags"}
TASK_CLASSIFICATIONS = ("interactive", "background", "unknown")

THRESHOLD = re.compile(r"\bauto_compact_scope_limit=Some\(([0-9]{1,12})\)")
CONTEXT_WINDOW = re.compile(r"\bfull_context_window_limit=Some\(([0-9]{1,12})\)")
CONTEXT_TOKENS = re.compile(r"\bauto_compact_scope_tokens=([0-9]{1,12})\b")
CONTEXT_SCOPE = re.compile(r"\bauto_compact_limit_scope=([A-Za-z][A-Za-z0-9_]{0,47})\b")
TURN_MODEL = re.compile(r'\bturn\{(?:[^}]*?\s)?model="?([a-zA-Z0-9_.:/-]{1,128})(?=["\s}])')
CONTEXT_LIMIT = re.compile(r'\breason="?ContextLimit\b')
PHASE = re.compile(r'\bphase="?(PreTurn|MidTurn)\b')
TURN_ID = re.compile(r'\bturn\.id="?([0-9a-f-]{36})\b', re.I)
MODEL_ROUTE = re.compile(r"/responses(?:/compact)?(?=$|[\s\"',?}])")
HTTP_ERROR = re.compile(r"\bstatus=[45][0-9]{2}\b|\brequest failed\b", re.I)
HTTP_SUCCESS = re.compile(r"\bstatus=2[0-9]{2}\b")
TOOL_DURATION = re.compile(r"\btotal_duration_ms=([0-9]{1,15}(?:\.[0-9]{1,6})?)\b")
STREAM_ERROR = re.compile(
    r"stream disconnected|transport error|error (?:reading|processing).*stream|"
    r"stream error|websocket error|error sending request|connection (?:closed|reset)|unexpected eof",
    re.I,
)
POWER_EVENT = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})\s+"
    r"(Sleep|Wake|DarkWake)\s+(Entering Sleep state|(?:Dark)?Wake from)(.*)$"
)
POWER_ASSERTION = re.compile(
    r"^\s*pid\s+\d+\((?:ChatGPT|Codex)\):[^\n]*"
    r"\b(?:PreventUserIdleSystemSleep|PreventSystemSleep|NoIdleSleepAssertion)\b", re.M,
)


@contextmanager
def _open_readonly(database: Path):
    uri = database.absolute().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.3)) as connection:
        deadline = time.monotonic() + QUERY_TIMEOUT
        connection.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        yield connection


def _query_rows(
    connection: sqlite3.Connection, cutoff: float, now: float, *, task_only: bool = False,
) -> sqlite3.Cursor | None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(logs)")}
    if not {"ts", "level", "target", "feedback_log_body"} <= columns:
        return None
    if task_only and "thread_id" not in columns:
        return None

    nanos = (
        "CASE WHEN typeof(ts_nanos) = 'integer' AND ts_nanos BETWEEN 0 AND 999999999 THEN ts_nanos ELSE 0 END"
        if "ts_nanos" in columns else "0"
    )
    thread = "thread_id" if "thread_id" in columns else "NULL"
    process = "process_uuid" if "process_uuid" in columns else "NULL"
    order = ", ".join(f"{name} DESC" for name in ("ts", "ts_nanos", "id") if name in columns)
    targets = TASK_TARGETS if task_only else BODY_TARGETS
    markers = ", ".join("?" for _ in targets)
    # Apply the timestamp and row limit to the full log tail before filtering log types.
    # A missing timestamp index in another version is bounded by the progress handler.
    return connection.execute(
        f"""SELECT ts, {nanos}, target, level, {thread}, {process},
                   CASE WHEN target IN ({markers}) AND typeof(feedback_log_body) = 'text'
                        THEN substr(feedback_log_body, 1, ?) END
            FROM logs WHERE ts >= ? AND ts <= ? AND typeof(ts) = 'integer' ORDER BY {order} LIMIT ?""",
        (*sorted(targets), BODY_LIMIT, int(cutoff), int(now), ROW_LIMIT + 1),
    )


def _read_logs(
    codex_home: Path, cutoff: float, now: float, *, task_limit: int | None = None, task_scope: str = "all",
) -> tuple[dict, list[float]]:
    database = codex_home / "logs_2.sqlite"
    if not database.is_file():
        return {"status": "missing"}, []

    try:
        with _open_readonly(database) as connection:
            rows = _query_rows(connection, cutoff, now, task_only=task_limit is not None)
            if rows is None:
                return {"status": "unsupported_schema"}, []
            if task_limit is None:
                return _analyze_rows(rows, now)
            grouped, coverage = _group_task_rows(rows, now)
        return _format_tasks(codex_home, grouped, coverage, task_limit, task_scope), []
    except (OSError, sqlite3.Error, ValueError):
        return {"status": "unavailable"}, []


def _duration_summary(samples: list[float]) -> dict:
    return {
        "samples": len(samples),
        "median": round(median(samples), 2) if samples else None,
        "max": round(max(samples), 2) if samples else None,
    }


def _compaction_phase(body: str) -> str:
    phase = PHASE.search(body)
    return {"PreTurn": "pre_turn", "MidTurn": "mid_turn"}.get(phase[1] if phase else None, "other")


def _analyze_rows(rows: sqlite3.Cursor, now: float) -> tuple[dict, list[float]]:
    compactions = {"pre_turn": 0, "mid_turn": 0, "other": 0}
    thresholds = Counter()
    compaction_ended = {}
    compaction_samples = []
    model_headers = {}
    model_wait_samples = []
    tool_samples = []
    model_errors = []
    retries = http_errors = stream_errors = 0
    rows_scanned = 0
    truncated = False
    oldest_row = None

    # The database yields the newest row first. Match each compaction start to
    # its closest eligible end while keeping large log messages out of memory.
    for seconds, nanos, target, level, thread, process, body in rows:
        if rows_scanned == ROW_LIMIT:
            truncated = True
            break
        rows_scanned += 1
        oldest_row = seconds
        if not body:
            continue
        timestamp = seconds + (nanos or 0) / 1_000_000_000
        if thread and target in {COMPACTION_START_TARGET, HTTP_TARGET}:
            turn = TURN_ID.search(body)
            if turn:
                key = (thread, process, turn[1])
                if (
                    target == HTTP_TARGET and "run_sampling_request{" in body
                    and "auto_compact{" not in body
                    and "Request completed" in body and MODEL_ROUTE.search(body) and HTTP_SUCCESS.search(body)
                ):
                    model_headers[key] = timestamp
                elif (
                    target == COMPACTION_START_TARGET and "building step world state" in body
                    and "auto_compact{" not in body
                ):
                    headers = model_headers.pop(key, None)
                    if headers is not None and 0 <= headers - timestamp <= MAX_MODEL_WAIT_SECONDS:
                        model_wait_samples.append(headers - timestamp)

        if target == TURN_TARGET and level == "TRACE":
            threshold = THRESHOLD.search(body)
            if threshold:
                thresholds[int(threshold[1])] += 1
        if target == TOOL_TARGET:
            duration = TOOL_DURATION.search(body)
            if duration:
                tool_samples.append(float(duration[1]))

        if target in {"feedback_tags", COMPACTION_START_TARGET, COMPACTION_END_TARGET} and CONTEXT_LIMIT.search(body):
            phase = _compaction_phase(body)
            if target == "feedback_tags":
                # One response telemetry row per observed request on supported versions.
                if level == "INFO":
                    compactions[phase] += 1
            elif thread and (turn := TURN_ID.search(body)):
                key = (thread, process, turn[1], phase)
                if target == COMPACTION_END_TARGET and "context history item counts" in body:
                    compaction_ended[key] = timestamp
                elif target == COMPACTION_START_TARGET and "building step world state" in body:
                    ended = compaction_ended.pop(key, None)
                    if ended is not None and 0 <= ended - timestamp <= MAX_COMPACTION_SECONDS:
                        compaction_samples.append(ended - timestamp)

        if target == RETRY_TARGET and level in {"WARN", "ERROR"}:
            retries += 1
        elif target == HTTP_TARGET and MODEL_ROUTE.search(body) and HTTP_ERROR.search(body):
            http_errors += 1
        elif target in STREAM_TARGETS and STREAM_ERROR.search(body):
            stream_errors += 1
        else:
            continue
        model_errors.append(timestamp)

    return {
        "status": "ok",
        "rows_scanned": rows_scanned,
        "truncated": truncated,
        "oldest_row_age_seconds": round(max(0, now - oldest_row)) if oldest_row is not None else None,
        "compaction_requests": compactions,
        "compaction_log_elapsed_seconds": _duration_summary(compaction_samples),
        "model_step_to_http_headers_seconds": {
            **_duration_summary(model_wait_samples),
            "at_least_30_seconds": sum(value >= 30 for value in model_wait_samples),
            "at_least_120_seconds": sum(value >= 120 for value in model_wait_samples),
        },
        "tool_call_elapsed_ms": _duration_summary(tool_samples),
        "effective_threshold_observations": {str(value): thresholds[value] for value in sorted(thresholds)},
        "model_retry_rows": retries,
        "model_http_error_rows": http_errors,
        "model_stream_error_rows": stream_errors,
    }, model_errors[::-1]


def _observation_status(values) -> str:
    known = len(values) - (None in values)
    if known > 1:
        return "mixed"
    if known == 0:
        return "missing"
    return "partial" if None in values else "single"


@dataclass
class _TaskObservations:
    thresholds: Counter[int | None] = field(default_factory=Counter)
    windows: Counter[int] = field(default_factory=Counter)
    models: set[str | None] = field(default_factory=set)
    context_scopes: set[str | None] = field(default_factory=set)
    context_samples: int = 0
    context_max: int | None = None
    paired_samples: int = 0
    above_threshold_samples: int = 0
    compactions: Counter[str] = field(default_factory=Counter)

    def add_turn(self, body: str, threshold_match, tokens_match, window_match) -> None:
        model = TURN_MODEL.search(body)
        threshold = int(threshold_match[1]) if threshold_match else None
        self.thresholds[threshold] += 1
        self.models.add(model[1] if model else None)
        if window_match:
            self.windows[int(window_match[1])] += 1
        if tokens_match:
            scope = CONTEXT_SCOPE.search(body)
            tokens = int(tokens_match[1])
            self.context_scopes.add(scope[1] if scope else None)
            self.context_samples += 1
            self.context_max = tokens if self.context_max is None else max(tokens, self.context_max)
            if threshold is not None:
                self.paired_samples += 1
                self.above_threshold_samples += tokens > threshold

    def report(self, task: str, classification: str) -> dict:
        thresholds = sorted(value for value in self.thresholds if value is not None)
        return {
            "task": task,
            "classification": classification,
            "threshold_status": _observation_status(self.thresholds),
            "model_status": _observation_status(self.models),
            "context_scope_status": _observation_status(self.context_scopes),
            "effective_threshold_observations": {str(value): self.thresholds[value] for value in thresholds},
            "full_context_window_observations": {str(value): self.windows[value] for value in sorted(self.windows)},
            "context_scope_tokens": {
                "samples": self.context_samples,
                "max": self.context_max,
                "paired_threshold_samples": self.paired_samples,
                "above_threshold_samples": self.above_threshold_samples,
            },
            "compaction_requests": {phase: self.compactions[phase] for phase in ("pre_turn", "mid_turn", "other")},
        }


def _group_task_rows(rows: sqlite3.Cursor, now: float) -> tuple[dict[str, _TaskObservations], dict]:
    grouped = defaultdict(_TaskObservations)
    rows_scanned = unattributed = 0
    truncated = False
    oldest_row = None
    for seconds, _, target, level, thread, _, body in rows:
        if rows_scanned == ROW_LIMIT:
            truncated = True
            break
        rows_scanned += 1
        oldest_row = seconds
        if not body:
            continue

        if target == TURN_TARGET and level == "TRACE":
            threshold = THRESHOLD.search(body)
            tokens = CONTEXT_TOKENS.search(body)
            window = CONTEXT_WINDOW.search(body)
            if not (threshold or tokens or window):
                continue
            if thread:
                grouped[thread].add_turn(body, threshold, tokens, window)
            else:
                unattributed += 1
        elif target == "feedback_tags" and level == "INFO" and CONTEXT_LIMIT.search(body):
            if thread:
                grouped[thread].compactions[_compaction_phase(body)] += 1
            else:
                unattributed += 1

    return grouped, {
        "rows_scanned": rows_scanned,
        "truncated": truncated,
        "oldest_row_age_seconds": round(max(0, now - oldest_row)) if oldest_row is not None else None,
        "unattributed_observations": unattributed,
    }


def _state_value(value: str | None):
    if isinstance(value, str) and value.startswith(("{", '"')):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            return None
    return value


def _classify_task(source_text: str | None, thread_source_text: str | None) -> str:
    source = _state_value(source_text)
    thread_source = _state_value(thread_source_text)
    background = ("automation", "subagent", "auto-review", "codex-auto-review")
    if thread_source in background or source in background or (isinstance(source, dict) and "subagent" in source):
        return "background"
    if thread_source == "user" or (thread_source_text is None and source in ("cli", "desktop", "manual")):
        return "interactive"
    return "unknown"


def _thread_classifications(codex_home: Path, thread_ids: list[str]) -> tuple[str, dict[str, str]]:
    if not thread_ids:
        return "ok", {}
    database = codex_home / "state_5.sqlite"
    if not database.is_file():
        return "missing", {}
    classified = {}
    batch_size = 500
    try:
        with _open_readonly(database) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
            if "id" not in columns or not {"source", "thread_source"} & columns:
                return "unsupported_schema", {}
            source = "substr(source, 1, 8192)" if "source" in columns else "NULL"
            thread_source = "substr(thread_source, 1, 128)" if "thread_source" in columns else "NULL"
            for start in range(0, len(thread_ids), batch_size):
                batch = thread_ids[start:start + batch_size]
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT id, {source}, {thread_source} FROM threads WHERE id IN ({placeholders})", batch,
                )
                for thread, raw_source, raw_thread_source in rows:
                    classified[thread] = _classify_task(raw_source, raw_thread_source)
    except (OSError, sqlite3.Error, ValueError):
        return "unavailable", {}
    return "ok", classified


def _format_tasks(codex_home: Path, grouped: dict[str, _TaskObservations], coverage: dict, limit: int, scope: str) -> dict:
    classification_status, classifications = _thread_classifications(codex_home, list(grouped))
    counts = dict.fromkeys(TASK_CLASSIFICATIONS, 0)
    tasks = []
    matched = 0
    for thread, evidence in grouped.items():
        classification = classifications.get(thread, "unknown")
        counts[classification] += 1
        if scope not in ("all", classification):
            continue
        matched += 1
        if len(tasks) < limit:
            tasks.append(evidence.report(f"task_{matched:02d}", classification))
    return {
        "status": "ok", **coverage,
        "classification_status": classification_status,
        "threads_observed": len(grouped),
        "threads_by_scope": counts,
        "threads_matched": matched,
        "threads_omitted": matched - len(tasks),
        "tasks": tasks,
    }


def _sleep_intervals(output: str) -> list[tuple[float, float, str]]:
    intervals = []
    start = None
    reason = "other"
    for line in output.splitlines():
        event = POWER_EVENT.match(line)
        if not event:
            continue
        try:
            timestamp = datetime.strptime(event[1], "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            continue
        if event[2] == "Sleep" and start is None:
            start = timestamp
            detail = event[4].casefold()
            reason = "idle" if "'idle sleep'" in detail else (
                "lid" if "'clamshell sleep'" in detail else "other"
            )
        elif event[2] == "Wake" and start is not None:
            if timestamp >= start:
                intervals.append((start, timestamp, reason))
            start = None
    return intervals


def _pmset(command: str) -> str | None:
    try:
        result = subprocess.run(
            ["/usr/bin/pmset", "-g", command], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=True, text=True, errors="replace", timeout=PMSET_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def _read_power(cutoff: float, now: float, model_errors: list[float], logs_available: bool) -> dict:
    if sys.platform != "darwin":
        return {"status": "unsupported_platform", "assertions_status": "unsupported_platform"}

    output = _pmset("log")
    assertions = _pmset("assertions")
    report = {
        "status": "ok" if output is not None else "unavailable",
        "assertions_status": "ok" if assertions is not None else "unavailable",
        "codex_idle_sleep_assertion": bool(POWER_ASSERTION.search(assertions)) if assertions is not None else None,
    }
    if output is None:
        return report

    intervals = [(start, end, reason) for start, end, reason in _sleep_intervals(output) if end >= cutoff and start <= now]
    reasons = Counter(reason for _, _, reason in intervals)
    correlated = 0
    near_wake = set()
    for start, end, _ in intervals:
        first = bisect_left(model_errors, max(start, cutoff))
        after = bisect_right(model_errors, min(end + WAKE_GRACE_SECONDS, now))
        if first < after:
            correlated += 1
        before_wake = bisect_left(model_errors, max(end - WAKE_GRACE_SECONDS, cutoff))
        near_wake.update(range(before_wake, after))

    report.update({
        "sleep_intervals": len(intervals),
        "sleep_reasons": {reason: reasons[reason] for reason in ("idle", "lid", "other")},
        "longest_sleep_seconds_in_window": round(max(
            (min(end, now) - max(start, cutoff) for start, end, _ in intervals), default=0,
        )),
        "sleep_intervals_with_model_errors": correlated if logs_available else None,
        "model_error_rows_near_wake": len(near_wake) if logs_available else None,
    })
    return report


def collect_diagnostics(codex_home: Path, hours: int) -> dict:
    """Report only aggregate observations for the requested recent time window."""
    now = time.time()
    cutoff = now - hours * 3600
    logs, model_errors = _read_logs(codex_home, cutoff, now)
    power = _read_power(cutoff, now, model_errors, logs["status"] == "ok")
    return {"window_hours": hours, "logs": logs, "power": power}


def collect_tasks(codex_home: Path, hours: int, limit: int, scope: str = "all") -> dict:
    """Compare runtime observations grouped by anonymous log thread within one report."""
    if not (hours > 0 and limit > 0) or scope not in ("all", *TASK_CLASSIFICATIONS):
        raise ValueError("invalid task report window, limit, or scope")
    now = time.time()
    report, _ = _read_logs(codex_home, now - hours * 3600, now, task_limit=limit, task_scope=scope)
    return {"window_hours": hours, "scope": "log_threads", "filter": scope, **report}
