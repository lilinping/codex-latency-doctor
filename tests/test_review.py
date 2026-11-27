import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codex_latency_doctor.__main__ import main
from codex_latency_doctor.review import review_turns


class ReviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.db = sqlite3.connect(self.home / "thread_history_1.sqlite")
        self.addCleanup(self.db.close)
        # There is deliberately no payload or transcript column.
        self.db.executescript("""
            CREATE TABLE thread_turns(thread_id TEXT, turn_id TEXT, rollout_ordinal INTEGER,
                rollout_end_ordinal INTEGER, completed_at INTEGER, status TEXT, duration_ms INTEGER);
            CREATE TABLE thread_items(thread_id TEXT, turn_id TEXT, item_type TEXT);
        """)

    def turn(self, thread, turn, start, end, duration=300):
        self.db.execute("INSERT INTO thread_turns VALUES (?, ?, ?, ?, ?, 'completed', ?)",
                        (thread, turn, start, end, 100 if end is not None else None, duration))
        self.db.commit()

    def review(self, after=None, limit=2, thread="private-chat"):
        return review_turns(self.home, thread, after, limit)

    def test_cli_returns_recent_metadata_only_and_marks_missing_measurements(self):
        for i in range(3):
            self.turn("private-chat", f"private-turn-{i}", i * 10, i * 10 + 8, None if i == 2 else 300)
        self.turn("other", "unrelated", 40, 45)
        self.db.executemany("INSERT INTO thread_items VALUES ('private-chat','private-turn-1',?)",
                            [(k,) for k in ("contextCompaction", "imageView", "imageView", "commandExecution", "sleep")])
        self.db.commit()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--codex-home", str(self.home), "--json", "review", "private-chat", "--limit", "2"]), 0)
        report = json.loads(output.getvalue())
        self.assertNotIn("private-", output.getvalue())
        self.assertTrue(report["older_turns_omitted"])
        self.assertFalse(report["has_more"])
        first, last = report["turns"]
        self.assertEqual(first["events"], {"compactions": 1, "commands": 1, "image_views": 2, "sleep_calls": 1})
        self.assertIsNone(last["duration_ms"])
        self.assertIsNone(last["events"])
        self.assertIsNone(first["memory_reads"])
        cursor = report["next_cursor"]
        unchanged = self.review(cursor)
        self.assertEqual((unchanged["turns"], unchanged["next_cursor"]), ([], cursor))

    def test_index_reads_and_first_two_commands_after_each_compaction(self):
        self.db.execute("ALTER TABLE thread_items ADD COLUMN item_json TEXT")
        self.db.execute("ALTER TABLE thread_items ADD COLUMN rollout_ordinal INTEGER")
        self.turn("private-chat", "turn", 1, 50)
        target = (self.home / "memories" / "MEMORY.md").as_posix()
        cwd = (self.home / "project").as_posix()
        search = {"type": "search", "path": "MEMORY.md", "command": f'rg -n "private-needle" "{target}"'}
        read = {"type": "read", "path": target}
        other = {"type": "read", "path": "MEMORY.md"}
        pattern_only = {"type": "search", "path": "MEMORY.md", "command": f'rg -g "*.md" "{target}" ./docs/MEMORY.md'}
        mention = {"type": "unknown", "command": f'echo "cat {target}"'}
        listing = {"type": "search", "path": target, "command": f'rg --files "{target}"'}
        # None represents compaction, an empty list an ordinary shell action.
        sequence = [[read, search], None, [other], [search], [read], None, [mention], [pattern_only],
                    [read], None, None, [], [listing], None, [{"type": "read", "path": "$CODEX_HOME/memories/MEMORY.md"}]]
        for ordinal, actions in reversed(list(enumerate(sequence))):
            kind = "contextCompaction" if actions is None else "commandExecution"
            item = json.dumps({"commandActions": actions, "cwd": cwd, "aggregatedOutput": "private-output"})
            self.db.execute("INSERT INTO thread_items VALUES ('private-chat','turn',?,?,?)", (kind, item, ordinal))
        self.db.execute("INSERT INTO thread_items VALUES ('another-chat','turn','commandExecution','invalid',2)")
        self.db.execute("INSERT INTO thread_items VALUES ('private-chat','turn','imageView','invalid',15)")
        self.db.commit()
        report = self.review()
        self.assertEqual(report["turns"][0]["memory_reads"], {"global_index_commands": 5, "compactions_with_early_index_read": 2})
        self.assertNotIn("private-", str(report))
        self.assertNotIn("MEMORY.md", str(report))
        self.db.execute("UPDATE thread_items SET item_json='broken' WHERE thread_id='private-chat' AND rollout_ordinal=0")
        self.db.commit()
        self.assertIsNone(self.review()["turns"][0]["memory_reads"])

    def test_related_uses_exact_directory_and_task_terms_and_never_guesses_a_chat(self):
        project = self.home / "project A"
        db = sqlite3.connect(self.home / "state_5.sqlite")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE threads(id TEXT, cwd TEXT, title TEXT, name TEXT, first_user_message TEXT)")
        for tid, cwd, title, name in (
            ("current", project, "租期 Api_1", None), ("wanted", project, "租期", "API_1"),
            ("other-task", project, "支付 API_1", None), ("other-project", self.home / "project AB", "租期 API_1", None),
        ):
            db.execute("INSERT INTO threads VALUES (?,?,?,?, 'private-body')", (tid, str(cwd), title, name))
        db.commit()

        def related(*terms):
            output = io.StringIO()
            with patch.dict("os.environ", CODEX_THREAD_ID="current"), redirect_stdout(output):
                self.assertEqual(main(["--codex-home", str(self.home), "--json", "related", "--project", str(project), *terms]), 0)
            self.assertNotIn("private-body", output.getvalue())
            return json.loads(output.getvalue())

        self.assertEqual(related("租期", "api_1"), {"status": "ok", "match": "unique", "thread_id": "wanted"})
        self.assertEqual(related("api_1"), {"status": "ok", "match": "ambiguous"})
        self.assertEqual(related("private-body"), {"status": "ok", "match": "none"})
        self.assertEqual(related("api%1"), {"status": "ok", "match": "none"})

    def test_cursor_follows_completion_order_with_ties_and_a_late_finishing_turn(self):
        self.turn("private-chat", "active", 1, None, 80)
        self.turn("private-chat", "already-closed", 3, 7)
        cursor = self.review()["next_cursor"]
        self.turn("private-chat", "next", 11, 18, 310)
        self.turn("private-chat", "same-end", 12, 18, 410)
        self.db.execute("UPDATE thread_turns SET rollout_end_ordinal=24, completed_at=100 WHERE turn_id='active'")
        self.db.commit()
        page = self.review(cursor, limit=1)
        self.assertTrue(page["has_more"])
        self.assertEqual([t["duration_ms"] for t in page["turns"]], [310])
        last = self.review(page["next_cursor"])
        self.assertFalse(last["has_more"])
        self.assertEqual([t["duration_ms"] for t in last["turns"]], [410, 80])

    def test_empty_baseline_preserves_new_pages_and_cannot_cross_chats(self):
        self.turn("private-chat", "running", 2, None)
        self.turn("other", "running-too", 2, None)
        empty = self.review()
        self.assertEqual(empty["turns"], [])
        cursor = empty["next_cursor"]
        self.assertEqual(self.review(cursor, thread="other")["status"], "invalid_cursor")
        for i in range(3):
            self.turn("private-chat", f"turn-{i}", 6+i, 15+i, i)
        first = self.review(cursor, limit=1)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["turns"][0]["duration_ms"], 0)
        self.turn("other", "turn-0", 6, 15)
        self.assertEqual(self.review(first["next_cursor"], thread="other")["status"], "invalid_cursor")
        self.db.execute("UPDATE thread_turns SET rollout_end_ordinal=20 WHERE thread_id='private-chat' AND turn_id='turn-0'")
        self.db.commit()
        self.assertEqual(self.review(first["next_cursor"])["status"], "invalid_cursor")

    def test_missing_index_and_invalid_positions_do_not_restart_a_review(self):
        self.assertEqual(review_turns(self.home / "absent", "chat", None, 1)["status"], "missing")
        self.assertEqual(self.review()["status"], "not_indexed")
        self.turn("private-chat", "closed", 0, 3)
        for cursor in ("invalid", "9999999999999999999.0000000000000000"):
            self.assertEqual(self.review(cursor)["status"], "invalid_cursor")
        self.db.execute("DROP TABLE thread_items")
        self.db.commit()
        self.assertEqual(self.review()["status"], "unsupported_schema")

    def test_sidebar_chat_resolves_current_segment_and_detects_rotation(self):
        first, second = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
        self.turn("private-chat", "older", 1, 3, 50)
        self.turn(first, "segment-turn", 1, 3, 600)
        self.turn(second, "segment-turn", 1, 3, 900)
        state = sqlite3.connect(self.home / "state_5.sqlite")
        self.addCleanup(state.close)
        state.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT)")
        state.execute("INSERT INTO threads VALUES (?,?)", ("private-chat", rf"C:\archive\rollout-date-private-chat_{first}.jsonl.zst"))
        state.commit()
        page = self.review()
        self.assertEqual(page["coverage"], "current_segment")
        self.assertEqual([t["duration_ms"] for t in page["turns"]], [600])
        state.execute("UPDATE threads SET rollout_path=?", (f"/new/rollout-date-private-chat_{second}.jsonl",))
        state.commit()
        self.assertEqual(self.review(page["next_cursor"])["status"], "invalid_cursor")
        self.assertEqual(self.review()["turns"][0]["duration_ms"], 900)
