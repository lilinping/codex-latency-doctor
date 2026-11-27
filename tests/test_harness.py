import json
import tempfile
import unittest
from pathlib import Path

from codex_latency_doctor.config import ConfigError
from codex_latency_doctor.harness import recovery_harness


class RecoveryHarnessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.path = self.home / "hooks.json"

    def test_inspection_preview_and_absent_remove_write_nothing(self):
        self.assertEqual(recovery_harness(self.home, "status")["registration"], "absent")
        self.assertEqual(recovery_harness(self.home, "install", dry_run=True)["status"], "planned")
        self.assertEqual(recovery_harness(self.home, "remove")["status"], "unchanged")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_install_retry_and_remove_keep_other_events_and_user_trust_indices(self):
        other = {"matcher": "startup", "hooks": [{"type": "command", "command": "echo unrelated"}]}
        original = {"hooks": {"SessionStart": [other], "Stop": [{"hooks": []}]}}
        self.path.write_text(json.dumps(original), encoding="utf-8")
        self.assertEqual(recovery_harness(self.home, "install")["registration"], "registered")
        installed = self.path.read_bytes()
        self.assertEqual(recovery_harness(self.home, "install")["status"], "unchanged")
        self.assertEqual(self.path.read_bytes(), installed)
        doc = json.loads(installed)
        self.assertEqual(doc["hooks"]["SessionStart"][0], other)
        recovery = doc["hooks"]["SessionStart"][1]
        self.assertEqual(set(recovery["matcher"].split("|")), {"compact", "resume"})
        self.assertEqual(doc["hooks"]["Stop"], original["hooks"]["Stop"])

        doc["hooks"]["SessionStart"].append(other)
        self.path.write_text(json.dumps(doc), encoding="utf-8")
        recovery_harness(self.home, "remove")
        groups = json.loads(self.path.read_bytes())["hooks"]["SessionStart"]
        self.assertEqual(groups[0], groups[2])
        self.assertEqual(groups[1]["hooks"], [])
        self.assertEqual(recovery_harness(self.home, "status")["registration"], "absent")
        recovery_harness(self.home, "install")
        self.assertEqual(json.loads(self.path.read_bytes()), doc)

    def test_clean_install_and_remove_do_not_touch_native_trust(self):
        config = self.home / "config.toml"
        config.write_bytes(b"# owner settings\n")
        recovery_harness(self.home, "install")
        self.assertEqual(set(json.loads(self.path.read_bytes())["hooks"]), {"SessionStart"})
        before = self.path.read_bytes()
        self.assertEqual(recovery_harness(self.home, "remove", dry_run=True)["status"], "planned")
        self.assertEqual(self.path.read_bytes(), before)
        recovery_harness(self.home, "remove")
        self.assertEqual(json.loads(self.path.read_bytes())["hooks"]["SessionStart"], [])
        self.assertEqual(config.read_bytes(), b"# owner settings\n")

    def test_malformed_or_customized_hooks_do_not_get_overwritten(self):
        recovery_harness(self.home, "install")
        doc = json.loads(self.path.read_bytes())
        doc["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "echo custom"
        samples = [b'{"hooks":{},"hooks":{}}', b'{"hooks":null}', b'{broken', json.dumps(doc).encode()]
        for sample in samples:
            with self.subTest(sample=sample):
                self.path.write_bytes(sample)
                for action in ("install", "remove"):
                    with self.assertRaises(ConfigError):
                        recovery_harness(self.home, action)
                    self.assertEqual(self.path.read_bytes(), sample)
