"""Read a page of closed turns from one chat's local history projection."""

import hashlib
import re
import sqlite3
from pathlib import Path

from .diagnostics import _open_readonly


def _projection_id(codex_home: Path, thread_id: str) -> str:
    # The sidebar retains the chat ID when the native history index moves to a new segment.
    state = codex_home / "state_5.sqlite"
    if state.is_file():
        with _open_readonly(state) as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(threads)")}
            if {"id", "rollout_path"} <= columns:
                row = db.execute("SELECT rollout_path FROM threads WHERE id=?", (thread_id,)).fetchone()
                if row and isinstance(row[0], str):
                    name = Path(row[0].replace("\\", "/")).name
                    uuid = r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
                    segment = re.fullmatch(r"rollout-.+-" + re.escape(thread_id) + "_" + uuid + r"\.jsonl(?:\.zst)?", name)
                    if segment:
                        return segment[1]
    return thread_id


def _cursor(thread_id: str, row: sqlite3.Row | None) -> str:
    # Bind the position to this chat and this completion if the projection is rebuilt.
    position, anchor = "start", thread_id
    if row is not None:
        position = str(row["rollout_ordinal"])
        anchor = f"{thread_id}\0{row['turn_id']}\0{row['rollout_end_ordinal']}"
    tag = hashlib.sha256(anchor.encode()).hexdigest()[:16]
    return f"{position}.{tag}"


def review_turns(codex_home: Path, thread_id: str, after: str | None, limit: int) -> dict:
    database = codex_home / "thread_history_1.sqlite"
    if not database.is_file():
        return {"status": "missing"}
    if after is not None and not re.fullmatch(r"(?:start|[0-9]{1,19})\.[0-9a-f]{16}", after):
        return {"status": "invalid_cursor"}

    try:
        projection = _projection_id(codex_home, thread_id)
        scope = thread_id + "\0" + projection
        coverage = "current_segment" if projection != thread_id else "direct_index"
        with _open_readonly(database) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            turn_columns = {r["name"] for r in db.execute("PRAGMA table_info(thread_turns)")}
            item_columns = {r["name"] for r in db.execute("PRAGMA table_info(thread_items)")}
            required = {"thread_id", "turn_id", "rollout_ordinal", "rollout_end_ordinal",
                        "status", "completed_at", "duration_ms"}
            if not required <= turn_columns or not {"thread_id", "turn_id", "item_type"} <= item_columns:
                return {"status": "unsupported_schema"}
            if not db.execute("SELECT 1 FROM thread_turns WHERE thread_id=? LIMIT 1", (projection,)).fetchone():
                return {"status": "not_indexed", "coverage": coverage}

            params = [projection]
            boundary = ""
            order = "DESC" if after is None else "ASC"
            if after is not None and after != _cursor(scope, None):
                position = after.split(".")[0]
                if position == "start" or int(position) > 2**63 - 1:
                    return {"status": "invalid_cursor"}
                ordinal = int(position)
                anchor = db.execute(
                    "SELECT turn_id, rollout_ordinal, rollout_end_ordinal FROM thread_turns "
                    "WHERE thread_id=? AND rollout_ordinal=? AND completed_at IS NOT NULL "
                    "AND rollout_end_ordinal IS NOT NULL", (projection, ordinal),
                ).fetchone()
                if anchor is None or _cursor(scope, anchor) != after:
                    return {"status": "invalid_cursor", "coverage": coverage}
                boundary = "AND (rollout_end_ordinal, rollout_ordinal) > (?, ?)"
                params.extend([anchor["rollout_end_ordinal"], ordinal])

            rows = db.execute(
                "SELECT turn_id, rollout_ordinal, rollout_end_ordinal, status, completed_at, duration_ms "
                "FROM thread_turns WHERE thread_id=? AND completed_at IS NOT NULL AND rollout_end_ordinal IS NOT NULL "
                f"{boundary} ORDER BY rollout_end_ordinal {order}, rollout_ordinal {order} LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            extra = len(rows) > limit
            page = rows[:limit]
            if after is None:
                page.reverse()
            turns = []
            for row in page:
                counts = dict(db.execute(
                    "SELECT item_type, count(*) FROM thread_items WHERE thread_id=? AND turn_id=? GROUP BY item_type",
                    (projection, row["turn_id"]),
                ))
                turns.append({
                    "completed_at": row["completed_at"],
                    "status": row["status"] if row["status"] in ("completed", "failed", "interrupted") else "unknown",
                    "duration_ms": row["duration_ms"],
                    "events": {name: counts.get(kind, 0) for name, kind in (
                        ("compactions", "contextCompaction"), ("commands", "commandExecution"),
                        ("image_views", "imageView"), ("sleep_calls", "sleep"),
                    )} if counts else None,
                })
            return {
                "status": "ok", "coverage": coverage, "turns": turns,
                "next_cursor": _cursor(scope, page[-1]) if page else after or _cursor(scope, None),
                "older_turns_omitted": after is None and extra, "has_more": after is not None and extra,
            }
    except sqlite3.Error:
        return {"status": "unavailable"}
