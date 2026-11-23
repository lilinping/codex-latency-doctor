import stat
import tempfile
import tomllib
import unittest
from pathlib import Path

from codex_latency_doctor.config import (
    BACKUPS,
    ConfigError,
    change_config,
    inspect_config,
    restore_config,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.home = Path(workspace.name)
        self.path = self.home / "config.toml"

    def test_apply_and_rollback_preserve_all_other_bytes(self):
        original = (
            b"# comment\r\nmodel_auto_compact_token_limit = 80_000 # token cap\r\n"
            b"[desktop] # keep\r\npreventSleepWhileRunning = false\r\n"
            b'[mcp_servers.example.env]\r\nKEY = "sensitive-placeholder"\r\n'
        )
        self.path.write_bytes(original)
        self.path.chmod(0o640)
        result = change_config(self.home, compact_tokens=120000, prevent_idle_sleep=True)
        self.assertEqual(result["status"], "applied")
        expected = original.replace(b"80_000", b"120000").replace(b"= false", b"= true")
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        backup = self.home / BACKUPS / result["backup_id"] / "config.toml"
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(restore_config(self.home, result["backup_id"])["status"], "restored")
        self.assertEqual(self.path.read_bytes(), original)

    def test_add_missing_settings(self):
        self.path.write_text('[projects."/example"]\ntrust_level = "trusted"')
        change_config(self.home, compact_tokens=120000, prevent_idle_sleep=True)
        parsed = tomllib.loads(self.path.read_text())
        self.assertEqual(parsed["model_auto_compact_token_limit"], 120000)
        self.assertIs(parsed["desktop"]["preventSleepWhileRunning"], True)
        self.assertEqual(parsed["projects"]["/example"]["trust_level"], "trusted")

    def test_insert_into_empty_desktop_table(self):
        self.path.write_text("[desktop]")
        change_config(self.home, compact_tokens=None, prevent_idle_sleep=True)
        self.assertIs(inspect_config(self.home)["prevent_idle_sleep"], True)

    def test_reject_inline_table_and_invalid_toml(self):
        for old in ("desktop = { preventSleepWhileRunning = false }\n", "[desktop"):
            self.path.write_text(old)
            with self.assertRaises(ConfigError):
                change_config(self.home, compact_tokens=12, prevent_idle_sleep=True)
            self.assertEqual(self.path.read_text(), old)
            self.assertFalse((self.home / BACKUPS).exists())

    def test_conflicting_rollback_is_refused(self):
        self.path.write_text("")
        changed = change_config(self.home, compact_tokens=120000, prevent_idle_sleep=False)
        edited = self.path.read_text() + "\n# another edit\n"
        self.path.write_text(edited)
        with self.assertRaises(ConfigError):
            restore_config(self.home, changed["backup_id"])
        self.assertEqual(self.path.read_text(), edited)
        with self.assertRaises(ConfigError):
            restore_config(self.home, "../other")

    def test_symlink_cannot_be_written(self):
        target = self.home / "original.toml"
        target.write_text("")
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            change_config(self.home, compact_tokens=120000, prevent_idle_sleep=False)
        self.assertEqual(target.read_text(), "")

    def test_inspection_only_reports_allowlisted_fields(self):
        self.path.write_text(
            'model_provider = "confidential-name"\n[model_providers.confidential-name]\n'
            'base_url = "https://confidential.example"\n'
            '[mcp_servers.confidential-server.env]\nAPI_TOKEN = "secret"\n'
        )
        result = inspect_config(self.home)
        self.assertTrue(result["selected_custom_provider"])
        self.assertEqual(result["mcp_count"], 1)
        self.assertNotIn("confidential", str(result))
        self.assertNotIn("secret", str(result))
