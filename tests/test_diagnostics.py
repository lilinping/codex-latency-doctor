import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from codex_latency_doctor import diagnostics as d


class LogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "中文 #folder"
        self.home.mkdir()
        self.database = self.home / "logs_2.sqlite"

    def make_database(self, rows):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("""
                CREATE TABLE logs (id INTEGER PRIMARY KEY, ts INTEGER, ts_nanos INTEGER,
                    target TEXT, level TEXT, thread_id TEXT, process_uuid TEXT, feedback_log_body TEXT)
            """)
            connection.execute("CREATE INDEX idx_logs_ts ON logs (ts DESC, ts_nanos DESC, id DESC)")
            connection.executemany(
                "INSERT INTO logs (ts, ts_nanos, target, level, feedback_log_body, thread_id, process_uuid) "
                "VALUES (?, 0, ?, ?, ?, ?, 'private-process')",
                [row if len(row) == 5 else (*row, "private-thread") for row in rows],
            )

    def make_state(self, rows):
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as connection, connection:
            connection.execute("""
                CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT, thread_source TEXT, title TEXT)
            """)
            connection.executemany("INSERT INTO threads VALUES (?, ?, ?, 'private-title')", rows)

    def test_timings_and_counts_are_aggregate(self):
        turn = 'turn.id="00000000-0000-0000-0000-000000000001"'
        self.make_database([
            (10, d.TURN_TARGET, "TRACE", "auto_compact_scope_limit=Some(80000)"),
            (20, d.TURN_TARGET, "TRACE", "auto_compact_scope_limit=Some(120000)"),
            (25, d.COMPACTION_START_TARGET, "TRACE",
             turn + " reason=ContextLimit phase=MidTurn building step world state"),
            (27, "feedback_tags", "INFO", "reason=ContextLimit phase=MidTurn"),
            (32, d.COMPACTION_END_TARGET, "DEBUG",
             turn + " reason=ContextLimit phase=MidTurn context history item counts"),
            (35, d.RETRY_TARGET, "WARN", "private-url: model stream failed"),
            (40, d.HTTP_TARGET, "DEBUG", "path=/responses status=503"),
            (42, d.HTTP_TARGET, "DEBUG", "path=/mcp status=503"),
            (43, "tool", "WARN", "reason=ContextLimit phase=PreTurn stream disconnected total_duration_ms=8500"),
            (50, "codex_api::sse::responses", "DEBUG", "Error reading SSE, Transport error"),
            (60, d.TOOL_TARGET, "INFO", "Tool call completed total_duration_ms=500"),
        ])
        report, errors = d._read_logs(self.home, 19, 80)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["rows_scanned"], 10)
        self.assertEqual(report["compaction_requests"], {"pre_turn": 0, "mid_turn": 1, "other": 0})
        self.assertEqual(report["compaction_log_elapsed_seconds"], {"samples": 1, "median": 7.0, "max": 7.0})
        self.assertEqual(report["effective_threshold_observations"], {"120000": 1})
        self.assertEqual(report["model_retry_rows"], 1)
        self.assertEqual(report["model_http_error_rows"], 1)
        self.assertEqual(report["model_stream_error_rows"], 1)
        self.assertEqual(report["tool_call_elapsed_ms"], {"samples": 1, "median": 500.0, "max": 500.0})
        self.assertEqual(errors, [35.0, 40.0, 50.0])
        self.assertNotIn("private-", json.dumps(report))

    def test_reports_truncation_before_target_filtering(self):
        self.make_database([(second, "unrelated", "INFO", "noise") for second in range(1, 5)])
        with patch.object(d, "ROW_LIMIT", 2):
            report, _ = d._read_logs(self.home, 1, 5)
        self.assertEqual(report["rows_scanned"], 2)
        self.assertEqual(report["oldest_row_age_seconds"], 2)
        self.assertTrue(report["truncated"])

    def test_model_step_to_headers_pairs_same_turn_and_excludes_compaction(self):
        turn_a = 'turn.id="00000000-0000-0000-0000-000000000001"'
        turn_b = 'turn.id="00000000-0000-0000-0000-000000000002"'
        self.make_database([
            (20, d.COMPACTION_START_TARGET, "TRACE", turn_a + " building step world state"),
            (21, d.COMPACTION_START_TARGET, "TRACE", turn_a + " building step world state"),
            (23, d.COMPACTION_START_TARGET, "TRACE", turn_a + " auto_compact{ building step world state"),
            (25, d.HTTP_TARGET, "DEBUG", turn_a + " auto_compact{ Request completed path=/responses status=200"),
            (26, d.HTTP_TARGET, "DEBUG", turn_b + " run_sampling_request{ Request completed path=/responses status=200"),
            (31, d.HTTP_TARGET, "DEBUG", turn_a + " run_sampling_request{ Request failed path=/responses status=503"),
            (34, d.HTTP_TARGET, "DEBUG",
             turn_a + " run_sampling_request{ run_auto_compact{ Request completed path=/responses/compact status=200"),
            (141, d.HTTP_TARGET, "DEBUG", turn_a + " run_sampling_request{ Request completed path=/responses status=200"),
        ])
        report, _ = d._read_logs(self.home, 0, 200)
        self.assertEqual(report["model_step_to_http_headers_seconds"], {
            "samples": 1, "median": 120.0, "max": 120.0,
            "at_least_30_seconds": 1, "at_least_120_seconds": 1,
        })

    def test_ignores_unattributed_compaction_times_and_unrecognized_markers(self):
        self.make_database([
            (10, d.COMPACTION_START_TARGET, "TRACE", "reason=ContextLimit phase=MidTurn building step world state"),
            (15, "feedback_tags", "DEBUG", "reason=ContextLimit phase=MidTurn"),
            (16, d.TURN_TARGET, "DEBUG", "auto_compact_scope_limit=Some(42)"),
            (20, d.COMPACTION_END_TARGET, "DEBUG", "reason=ContextLimit phase=MidTurn context history item counts"),
        ])
        report, _ = d._read_logs(self.home, 0, 30)
        self.assertEqual(report["compaction_log_elapsed_seconds"]["samples"], 0)
        self.assertEqual(report["compaction_requests"]["mid_turn"], 0)
        self.assertEqual(report["effective_threshold_observations"], {})

    def test_malformed_sqlite_values_do_not_abort_scan(self):
        self.make_database([
            (10, d.TURN_TARGET, "TRACE", b"auto_compact_scope_limit=Some(1)"),
            (20, d.TURN_TARGET, "TRACE", "auto_compact_scope_limit=Some(80000)"),
            (25.5, d.TURN_TARGET, "TRACE", "auto_compact_scope_limit=Some(2)"),
        ])
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE logs SET ts_nanos = 'invalid nanos' WHERE ts = 20")
        report, _ = d._read_logs(self.home, 0, 30)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["effective_threshold_observations"], {"80000": 1})
        with patch.object(d.time, "time", return_value=30):
            task, = d.collect_tasks(self.home, 1, 1)["tasks"]
        self.assertEqual(task["effective_threshold_observations"], {"80000": 1})

    def test_missing_unsupported_and_unreadable_databases(self):
        self.assertEqual(d._read_logs(self.home, 0, 5)[0]["status"], "missing")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE logs (different_version TEXT)")
        self.assertEqual(d._read_logs(self.home, 0, 5)[0]["status"], "unsupported_schema")
        self.database.write_bytes(b"not a database")
        self.assertEqual(d._read_logs(self.home, 0, 5)[0]["status"], "unavailable")

    def test_anonymous_task_metrics_preserve_thread_and_same_row_pairing(self):
        turn_a = "turn{model=private-model-a} "
        turn_b = "turn{model=private-model-b} "
        a, b, c = "private-thread-a", "private-thread-b", "private-thread-c"
        self.make_database([
            (16000, d.TURN_TARGET, "TRACE", turn_a + "auto_compact_scope_limit=Some(1)", a),
            (17000, d.TURN_TARGET, "TRACE", turn_a + "auto_compact_scope_limit=Some(80000) "
             "auto_compact_scope_tokens=80000 auto_compact_limit_scope=Total full_context_window_limit=Some(475000)", a),
            (17100, d.TURN_TARGET, "TRACE", turn_b + "auto_compact_scope_limit=Some(120000) "
             "auto_compact_scope_tokens=120500 auto_compact_limit_scope=Total full_context_window_limit=Some(475000)", a),
            (17200, d.TURN_TARGET, "TRACE", "auto_compact_scope_tokens=130000", a),
            (17300, "feedback_tags", "INFO", "reason=ContextLimit phase=MidTurn", a),
            (17400, d.TURN_TARGET, "TRACE", turn_b + "auto_compact_scope_limit=Some(244800)", b),
            (17500, "feedback_tags", "INFO", "reason=ContextLimit phase=PreTurn", c),
            (17600, "feedback_tags", "INFO", "reason=ContextLimit phase=PreTurn", None),
            (17700, "tool", "TRACE", "auto_compact_scope_limit=Some(9)", "private-unrelated"),
        ])
        self.make_state([
            (a, "vscode", "user"),
            (b, '{"subagent":{"other":"private-originator"}}', None),
            (c, "vscode", None),
        ])
        with patch.object(d.time, "time", return_value=20000):
            report = d.collect_tasks(self.home, 1, 2)
            interactive = d.collect_tasks(self.home, 1, 1, "interactive")
        self.assertEqual(report["rows_scanned"], 8)
        self.assertEqual(report["threads_observed"], 3)
        self.assertEqual(report["threads_omitted"], 1)
        self.assertEqual(report["threads_by_scope"], {"interactive": 1, "background": 1, "unknown": 1})
        self.assertEqual(report["unattributed_observations"], 1)
        self.assertEqual([row["classification"] for row in report["tasks"]], ["unknown", "background"])
        row, = interactive["tasks"]
        self.assertEqual(interactive["threads_matched"], 1)
        self.assertEqual(interactive["threads_omitted"], 0)
        self.assertEqual(row["task"], "task_01")
        self.assertEqual(row["model_status"], "mixed")
        self.assertEqual(row["threshold_status"], "mixed")
        self.assertEqual(row["context_scope_status"], "partial")
        self.assertEqual(row["effective_threshold_observations"], {"80000": 1, "120000": 1})
        self.assertEqual(row["full_context_window_observations"], {"475000": 2})
        self.assertEqual(row["context_scope_tokens"], {
            "samples": 3, "max": 130000, "paired_threshold_samples": 2, "above_threshold_samples": 1,
        })
        self.assertEqual(row["compaction_requests"], {"pre_turn": 0, "mid_turn": 1, "other": 0})
        self.assertNotIn("private-", json.dumps([report, interactive]))

    def test_task_reports_handle_missing_support_and_scan_cap(self):
        self.assertEqual(d.collect_tasks(self.home, 1, 1)["status"], "missing")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE logs (ts INTEGER, level TEXT, target TEXT, feedback_log_body TEXT)")
        self.assertEqual(d.collect_tasks(self.home, 1, 1)["status"], "unsupported_schema")
        self.database.unlink()
        self.make_database([
            (12, d.TURN_TARGET, "TRACE", "auto_compact_scope_limit=Some(80000)"),
            (15, d.TURN_TARGET, "TRACE", "auto_compact_scope_limit=Some(120000)"),
            (17, "unrelated", "INFO", "noise"),
        ])
        with patch.object(d.time, "time", return_value=20), patch.object(d, "ROW_LIMIT", 2):
            unknown = d.collect_tasks(self.home, 1, 1, "unknown")
            interactive = d.collect_tasks(self.home, 1, 1, "interactive")
        self.assertTrue(unknown["truncated"])
        self.assertEqual(unknown["rows_scanned"], 2)
        self.assertEqual(unknown["classification_status"], "missing")
        self.assertEqual(unknown["tasks"][0]["effective_threshold_observations"], {"120000": 1})
        self.assertEqual(interactive["threads_matched"], 0)
        self.assertEqual(interactive["threads_by_scope"]["unknown"], 1)


class PowerTests(unittest.TestCase):
    def test_dark_wake_does_not_end_sleep_interval(self):
        text = "\n".join([
            "2026-01-01 10:00:00 +0800 Sleep  Entering Sleep state due to 'Idle Sleep'",
            "2026-01-01 10:10:00 +0800 DarkWake  DarkWake from Deep Idle",
            "2026-01-01 10:10:02 +0800 Sleep  Entering Sleep state due to 'Idle Sleep'",
            "2026-01-01 10:15:00 +0800 Wake  Wake from Deep Idle",
            "2026-01-01 10:18:00 +0800 Sleep  Entering Sleep state due to 'Clamshell Sleep'",
            "2026-01-01 10:20:00 +0800 Wake  Wake from Deep Idle",
        ])
        start = datetime.fromisoformat("2026-01-01T10:00:00+08:00").timestamp()
        assertions = 'pid 123(ChatGPT): [00000] 00:00:34 NoIdleSleepAssertion named: "Electron"'
        with patch.object(d.sys, "platform", "darwin"), patch.object(d, "_pmset", side_effect=[text, assertions]):
            report = d._read_power(start + 120, start + 1300, [start + 899, start + 918, start + 1245], True)
        self.assertEqual(report["sleep_intervals"], 2)
        self.assertEqual(report["sleep_reasons"], {"idle": 1, "lid": 1, "other": 0})
        self.assertEqual(report["longest_sleep_seconds_in_window"], 780)
        self.assertEqual(report["sleep_intervals_with_model_errors"], 1)
        self.assertEqual(report["model_error_rows_near_wake"], 2)
        self.assertTrue(report["codex_idle_sleep_assertion"])

    def test_power_errors_and_unsupported_platform(self):
        with patch.object(d.sys, "platform", "darwin"), patch.object(d, "_pmset", side_effect=["", None]):
            report = d._read_power(0, 100, [], False)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["assertions_status"], "unavailable")
        self.assertIsNone(report["codex_idle_sleep_assertion"])
        self.assertIsNone(report["sleep_intervals_with_model_errors"])
        with patch.object(d.sys, "platform", "linux"):
            report = d._read_power(0, 100, [], True)
        self.assertEqual(report["status"], "unsupported_platform")
