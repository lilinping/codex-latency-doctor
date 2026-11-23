import os
import stat
import tempfile
import tomllib
import unittest
from pathlib import Path

from codex_latency_doctor import config
from codex_latency_doctor.config import (
    BACKUPS,
    ConfigError,
    change_config,
    find_matching_backups,
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
        if os.name == "nt":
            with config._windows_descriptor("D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;OW)") as descriptor:
                config._set_windows_dacl(self.path, descriptor)
            original_acl = config._windows_acl_text(config._windows_acl(self.path))
        else:
            self.path.chmod(0o640)
        result = change_config(self.home, compact_tokens=120000, prevent_idle_sleep=True)
        self.assertEqual(result["status"], "applied")
        expected = original.replace(b"80_000", b"120000").replace(b"= false", b"= true")
        self.assertEqual(self.path.read_bytes(), expected)
        backup = self.home / BACKUPS / result["backup_id"] / "config.toml"
        self.assertEqual(backup.read_bytes(), original)
        if os.name == "nt":
            self.assertEqual(config._windows_acl_text(config._windows_acl(self.path)), original_acl)
            acl = config._windows_acl_text(config._windows_acl(backup))
            self.assertRegex(acl, r";;;(?:OW|S-1-3-4)\)")
            self.assertNotRegex(acl, r";;;(?:WD|AU|BU)\)")
        else:
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(restore_config(self.home, result["backup_id"])["status"], "restored")
        self.assertEqual(self.path.read_bytes(), original)
        if os.name == "nt":
            self.assertEqual(config._windows_acl_text(config._windows_acl(self.path)), original_acl)

    def test_concurrent_write_is_refused(self):
        self.path.write_bytes(b"")
        with config._locked(self.home, create=True):
            with self.assertRaisesRegex(ConfigError, "Another Latency Doctor change"):
                change_config(self.home, compact_tokens=120000, prevent_idle_sleep=False)
            self.assertEqual(self.path.read_bytes(), b"")
            self.assertFalse(list((self.home / BACKUPS).glob("backup-*")))
        self.assertEqual(change_config(self.home, compact_tokens=120000, prevent_idle_sleep=False)["status"], "applied")

    def test_existing_backup_directory_with_unexpected_permissions_is_refused(self):
        self.path.write_bytes(b"")
        directory = self.home / BACKUPS
        directory.mkdir()
        if os.name != "nt":
            directory.chmod(0o755)
        with self.assertRaises(ConfigError):
            change_config(self.home, compact_tokens=120000, prevent_idle_sleep=False)
        with self.assertRaises(ConfigError):
            find_matching_backups(self.home)
        self.assertEqual(self.path.read_bytes(), b"")

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

    def test_preview_and_apply_change_only_the_selected_settings(self):
        original = (
            b'# Global settings\r\nmodel_reasoning_effort = "high" # keep comment\r\n'
            b'[mcp_servers."example-prod"] # keep header\r\ncommand = "example"\r\n'
            b'[mcp_servers."example-prod".env]\r\nTOKEN = "private-placeholder"\r\n'
            b'[mcp_servers.other]\r\ncommand = "other"\r\nenabled = false\r\n'
        )
        self.path.write_bytes(original)
        settings = {"reasoning_effort": "medium", "mcp_name": "example-prod", "mcp_enabled": False}
        changes = {
            "model_reasoning_effort": {"before": "high", "after": "medium"},
            "mcp_servers.example-prod.enabled": {"before": True, "after": False},
        }
        preview = change_config(self.home, **settings, dry_run=True)
        self.assertEqual(preview, {"status": "planned", "changes": changes})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.home.iterdir()), [self.path])

        applied = change_config(self.home, **settings)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["changes"], changes)
        expected = original.replace(b'"high"', b'"medium"').replace(
            b'command = "example"', b'enabled = false\r\ncommand = "example"',
        )
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(restore_config(self.home, applied["backup_id"])["status"], "restored")
        self.assertEqual(self.path.read_bytes(), original)

    def test_enable_paused_mcp_and_skip_an_unchanged_setting(self):
        original = b"[mcp_servers.example]\nenabled = false # keep comment\n"
        self.path.write_bytes(original)
        unchanged = change_config(self.home, mcp_name="example", mcp_enabled=False)
        self.assertEqual(unchanged, {"status": "unchanged"})
        self.assertFalse((self.home / BACKUPS).exists())

        enabled = change_config(self.home, mcp_name="example", mcp_enabled=True)
        self.assertEqual(enabled["changes"], {"mcp_servers.example.enabled": {"before": False, "after": True}})
        self.assertEqual(self.path.read_bytes(), original.replace(b"false", b"true"))
        self.assertEqual(change_config(self.home, mcp_name="example", mcp_enabled=True), {"status": "unchanged"})
        self.assertEqual(len(list((self.home / BACKUPS).glob("backup-*"))), 1)

    def test_refuse_invalid_or_unsupported_changes_without_writing(self):
        original = b'mcp_servers = { example = { command = "example" } }\n'
        self.path.write_bytes(original)
        cases = (
            {"mcp_name": "example", "mcp_enabled": False, "reasoning_effort": "medium"},
            {"mcp_name": "missing", "mcp_enabled": False},
            {"mcp_name": "example.env", "mcp_enabled": False},
            {"mcp_name": "example"},
            {"reasoning_effort": "unknown"},
        )
        for settings in cases:
            with self.subTest(**settings):
                with self.assertRaises(ConfigError):
                    change_config(self.home, **settings)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertFalse((self.home / BACKUPS).exists())

    def test_find_backups_without_a_backup_directory_is_read_only(self):
        self.assertEqual(find_matching_backups(self.home), {"status": "ok", "matching_backup_ids": []})
        self.assertEqual(list(self.home.iterdir()), [])

    def test_find_backups_after_unchanged_apply_keeps_all_matches(self):
        self.path.write_bytes(b"")
        first = change_config(self.home, compact_tokens=120000)["backup_id"]
        change_config(self.home, compact_tokens=160000)
        second = change_config(self.home, compact_tokens=120000)["backup_id"]
        self.assertEqual(change_config(self.home, compact_tokens=120000), {"status": "unchanged"})

        directory = self.home / BACKUPS
        before = self.path.read_bytes()
        lock = directory / ".lock"
        lock.unlink()
        self.assertEqual(find_matching_backups(self.home), {"status": "ok", "matching_backup_ids": sorted([first, second])})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(lock.exists())

    def test_find_backups_excludes_unrestorable_original(self):
        self.path.write_bytes(b"")
        backup_id = change_config(self.home, compact_tokens=120000)["backup_id"]
        original = self.home / BACKUPS / backup_id / "config.toml"
        original.write_bytes(self.path.read_bytes())
        self.assertEqual(find_matching_backups(self.home)["matching_backup_ids"], [])
        original.write_bytes(b"[invalid")
        self.assertEqual(find_matching_backups(self.home)["matching_backup_ids"], [])
        original.unlink()
        self.assertEqual(find_matching_backups(self.home)["matching_backup_ids"], [])

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
        try:
            self.path.symlink_to(target)
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                self.skipTest("The Windows runner cannot create a symlink.")
            raise
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
