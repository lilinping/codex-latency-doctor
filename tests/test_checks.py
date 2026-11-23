import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_latency_doctor.checks import probe_network, verify_native


class NetworkTests(unittest.TestCase):
    @patch("codex_latency_doctor.checks.sys.platform", "linux")
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

    @patch("codex_latency_doctor.checks.sys.platform", "linux")
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


class WindowsChecksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def make_file(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    @patch("codex_latency_doctor.checks.sys.platform", "win32")
    @patch("codex_latency_doctor.checks.subprocess.run")
    def test_curl_resolves_absolute_executable_and_ignores_batch_alias(self, run):
        alias = self.make_file("Aliases", "curl.cmd")
        curl = self.make_file("Tools & utilities", "curl.exe")
        run.return_value = subprocess.CompletedProcess([], 0, "401 0.25", None)
        with patch("codex_latency_doctor.checks.os.get_exec_path",
                   return_value=[".", str(alias.parent), f'"{curl.parent}"']):
            result = probe_network("api", 1, compare_direct=True)

        self.assertEqual(result["status"], "ok")
        inherited, direct = run.call_args_list
        self.assertEqual(inherited.args[0][0], str(curl))
        self.assertIn(os.devnull, inherited.args[0])
        self.assertNotIn("--noproxy", inherited.args[0])
        self.assertEqual(direct.args[0][-4:-2], ["--noproxy", "*"])
        self.assertEqual(inherited.kwargs["stderr"], subprocess.DEVNULL)

    @patch("codex_latency_doctor.checks.sys.platform", "win32")
    @patch("codex_latency_doctor.checks.subprocess.run")
    def test_verify_uses_native_executable_instead_of_adjacent_shim(self, run):
        codex = self.make_file("Native", "codex.exe")
        self.make_file("Native", "codex.cmd")
        run.return_value = subprocess.CompletedProcess([], 0)
        with patch("codex_latency_doctor.checks.os.get_exec_path", return_value=[str(codex.parent)]):
            result = verify_native(self.root / "My Codex & data")

        self.assertEqual(result, {"status": "accepted"})
        self.assertEqual(run.call_args.args[0], [
            str(codex), "app-server", "--strict-config", "--print-startup-config",
        ])
        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(self.root / "My Codex & data"))
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)

    @patch("codex_latency_doctor.checks.sys.platform", "win32")
    @patch("codex_latency_doctor.checks.subprocess.run")
    def test_verify_invokes_npm_entrypoint_without_a_command_shell(self, run):
        node = self.make_file("Node & tools", "node.exe")
        run.return_value = subprocess.CompletedProcess([], 0)
        for shim_parts, module_parts in (
            (("npm %prefix%",), ("npm %prefix%", "node_modules")),
            (("Project", "node_modules", ".bin"), ("Project", "node_modules")),
        ):
            with self.subTest(shim_dir=shim_parts):
                shim = self.make_file(*shim_parts, "codex.cmd")
                script = self.make_file(*module_parts, "@openai", "codex", "bin", "codex.js")
                with patch("codex_latency_doctor.checks.os.get_exec_path",
                           return_value=[str(shim.parent), str(node.parent)]):
                    result = verify_native(self.root)

                self.assertEqual(result, {"status": "accepted"})
                self.assertEqual(run.call_args.args[0], [
                    str(node), str(script), "app-server", "--strict-config", "--print-startup-config",
                ])
                self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("codex_latency_doctor.checks.sys.platform", "win32")
    @patch("codex_latency_doctor.checks.subprocess.run")
    def test_verify_reports_unsupported_shim_without_executing_it(self, run):
        shim = self.make_file("Wrappers", "codex.cmd")
        self.make_file("Wrappers", "node.exe")
        with patch("codex_latency_doctor.checks.os.get_exec_path", return_value=[str(shim.parent)]):
            result = verify_native(self.root)

        self.assertEqual(result, {"status": "codex_not_runnable"})
        run.assert_not_called()
