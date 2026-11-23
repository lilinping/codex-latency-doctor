import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from codex_latency_doctor.__main__ import main
from codex_latency_doctor.comparison import compare_audits


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.before = Path(temporary.name) / "比较 before.json"
        self.after = Path(temporary.name) / "比较 after.json"

    @staticmethod
    def snapshot(hour, median=12.5):
        return {
            "window_hours": 1,
            "captured_at_utc": f"2026-01-01T{hour:02d}:00:00+00:00",
            "config": {"compact_tokens": 80000, "private-key": "never echo"},
            "power": {"status": "unsupported_platform"},
            "logs": {
                "status": "ok",
                "rows_scanned": 23,
                "oldest_row_age_seconds": 2900,
                "truncated": False,
                "model_step_to_http_headers_seconds": {"samples": 4, "median": median},
                "compaction_requests": {"pre_turn": 2, "mid_turn": 1, "other": 0},
                "tool_call_elapsed_ms": {"samples": 0, "median": None},
                "effective_threshold_observations": {"80000": 3, "secret-url": 1},
            },
        }

    def write(self, before, after):
        self.before.write_text(json.dumps(before), encoding="utf-8-sig")
        # Windows PowerShell 5.1's default native command redirection uses UTF-16LE with a BOM.
        self.after.write_text(json.dumps(after), encoding="utf-16")

    def test_cli_reads_windows_export_and_compares_only_allowlisted_metrics(self):
        self.write(self.snapshot(8), self.snapshot(10, median=9))
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--json", "compare", str(self.before), str(self.after)]), 0)
        raw = output.getvalue()
        report = json.loads(raw)
        self.assertTrue(report["deltas_available"])
        self.assertNotIn("never echo", raw)
        self.assertNotIn("secret-url", raw)
        self.assertEqual(report["baseline"]["observed_compact_tokens"], [80000])
        self.assertEqual(report["metrics"]["model_header_median"], {
            "before": 12.5, "after": 9, "change": -3.5, "before_samples": 4, "after_samples": 4,
        })
        self.assertIsNone(report["metrics"]["tool_median"]["change"])
        self.assertEqual(report["metrics"]["tool_median"]["before_samples"], 0)

    def test_overlap_unequal_windows_or_truncation_suppresses_changes(self):
        for case in ("overlap", "unequal", "truncated", "missing", "empty"):
            with self.subTest(case=case):
                before, after = self.snapshot(8), self.snapshot(10, median=9)
                if case == "overlap":
                    after = self.snapshot(9, median=9)
                elif case == "unequal":
                    before["window_hours"] = 2
                elif case == "truncated":
                    before["logs"]["truncated"] = True
                elif case == "missing":
                    before["logs"] = {"status": "missing"}
                elif case == "empty":
                    before["logs"]["rows_scanned"] = 0
                self.write(before, after)
                report = compare_audits(self.before, self.after)
                self.assertFalse(report["deltas_available"])
                self.assertIsNone(report["metrics"]["model_header_median"]["change"])
                self.assertEqual(report["metrics"]["model_header_median"]["after"], 9)
                if case in ("missing", "empty"):
                    self.assertIsNone(report["metrics"]["compaction_requests"]["before"])

    def test_legacy_snapshots_work_and_task_report_is_rejected(self):
        before, after = self.snapshot(8), self.snapshot(10)
        del before["captured_at_utc"]
        self.write(before, after)
        report = compare_audits(self.before, self.after)
        self.assertTrue(report["deltas_available"])
        self.assertTrue(report["notes"])

        self.write({"window_hours": 1, "status": "ok", "tasks": []}, after)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--json", "compare", str(self.before), str(self.after)]), 1)
        self.assertEqual(json.loads(output.getvalue())["message"], "Baseline must come from --json audit.")
