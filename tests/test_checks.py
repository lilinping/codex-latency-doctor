import subprocess
import unittest
from unittest.mock import patch

from codex_latency_doctor.checks import probe_network


class NetworkTests(unittest.TestCase):
    @patch("codex_latency_doctor.checks.subprocess.run")
    @patch("codex_latency_doctor.checks.shutil.which", return_value="/usr/bin/curl")
    def test_all_failed_routes_report_no_http_response(self, _which, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 28, "000 0.0", "network timeout"),
            subprocess.CompletedProcess([], 0, "000 0.0", ""),
        ]
        result = probe_network("api", 1, compare_direct=True)
        self.assertEqual(result["status"], "no_http_response")
        self.assertEqual(result["environment_route"]["samples"], [{"status": "timeout"}])
        self.assertEqual(result["direct_route"]["samples"], [{"status": "unexpected_curl_output"}])
        self.assertEqual(result["direct_route"]["http_responses"], 0)

    @patch("codex_latency_doctor.checks.subprocess.run")
    @patch("codex_latency_doctor.checks.shutil.which", return_value="/usr/bin/curl")
    def test_http_error_responses_still_demonstrate_reachability(self, _which, run):
        for code in (401, 503):
            with self.subTest(code=code):
                run.side_effect = [
                    subprocess.CompletedProcess([], 7, "000 0.0", ""),
                    subprocess.CompletedProcess([], 0, f"{code} 0.1", ""),
                ]
                result = probe_network("chatgpt", 1, compare_direct=True)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["environment_route"]["http_responses"], 0)
                self.assertEqual(result["direct_route"]["samples"], [
                    {"status": "http_response", "http_status": code, "ttfb_ms": 100.0},
                ])
