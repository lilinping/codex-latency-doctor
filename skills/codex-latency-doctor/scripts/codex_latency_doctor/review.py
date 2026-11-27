"""Read a page of closed turns from one chat's local history projection."""

import hashlib
import json
import os
import re
import shlex
import sqlite3
from pathlib import Path

from .diagnostics import _open_readonly


def related_thread(codex_home: Path, project: Path, terms: list[str], current: str | None) -> dict:
    """Locate one prior chat by working directory and literal task terms, without opening it."""
    database = codex_home / "state_5.sqlite"
    if not database.is_file():
        return {"status": "missing"}
    terms = list(dict.fromkeys(term.strip().lower() for term in terms if term.strip()))
    if not 1 <= len(terms) <= 6:
        return {"status": "invalid_terms"}
    project = project.expanduser()
    directories = (str(project.absolute()), str(project.resolve()))
    directory = "cwd IN (?, ?)"
    if os.name == "nt":
        directory = "replace(cwd, '/', char(92)) COLLATE NOCASE IN (?, ?)"
    try:
        with _open_readonly(database) as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(threads)")}
            if not {"id", "cwd", "title"} <= columns:
                return {"status": "unsupported_schema"}
            label = "coalesce(title, '')"
            if "name" in columns:
                label += " || ' ' || coalesce(name, '')"
            filters = [directory, "id <> ?", *(f"instr(lower({label}), ?) > 0" for _ in terms)]
            if "archived" in columns:
                # Codex indexes the working directory after the archive flag. Include both states.
                filters.append("archived IN (0, 1)")
            rows = db.execute("SELECT id FROM threads WHERE " + " AND ".join(filters) + " LIMIT 2",
                              (*directories, current or "", *terms)).fetchall()
    except sqlite3.Error:
        return {"status": "unavailable"}
    if len(rows) == 1:
        return {"status": "ok", "match": "unique", "thread_id": rows[0][0]}
    return {"status": "ok", "match": "ambiguous" if rows else "none"}


def _is_global_index(value: str | None, cwd: str | None, codex_home: Path) -> bool:
    # Expand only the standard home prefixes, without touching the files being reviewed.
    if not isinstance(value, str) or not value:
        return False
    if os.name == "nt":
        value = value.replace("\\", "/")
    prefix, _, rest = value.partition("/")
    if prefix in {"$CODEX_HOME", "${CODEX_HOME}", "$env:CODEX_HOME", "%CODEX_HOME%"}:
        path = codex_home / rest
    elif prefix in {"~", "$HOME", "${HOME}", "$env:USERPROFILE", "%USERPROFILE%"}:
        path = Path.home() / rest
    else:
        path = Path(value)
        if not path.is_absolute():
            if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                return False
            path = Path(cwd) / path
    target = codex_home.absolute() / "memories" / "MEMORY.md"
    return Path(os.path.normpath(path)) == Path(os.path.normpath(target))


def _search_reads_index(action: dict, cwd: str | None, codex_home: Path) -> bool:
    # Native rg/grep search actions can retain only the basename in their "path" field.
    command = action.get("command")
    if not isinstance(command, str) or "memory.md" not in command.lower():
        return False
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    if os.name == "nt":
        lexer.escape = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return False
    if not tokens or "--files" in tokens:
        return False
    if Path(tokens[0]).name not in {"rg", "grep", "egrep", "fgrep", "rg.exe", "grep.exe"}:
        return _is_global_index(action.get("path"), cwd, codex_home)
    operands = []
    pattern_option = False
    options = True
    args = iter(tokens[1:])
    for arg in args:
        if arg in {"|", "||", "&&", ";", ">", ">>", "2>"}:
            break
        if options and arg == "--":
            options = False
        elif options and arg.startswith("-"):
            option = arg.partition("=")[0]
            if option in {"-e", "--regexp", "-f", "--file"} or arg.startswith(("-e", "-f")) and not arg.startswith("--"):
                pattern_option = True
            if arg in {"-e", "--regexp", "-f", "--file", "-g", "--glob", "--iglob", "-t", "-T", "--type",
                       "--type-not", "-A", "-B", "-C", "--after-context", "--before-context", "--context",
                       "-m", "--max-count", "--include", "--exclude", "--exclude-dir", "--color",
                       "--encoding", "--sort", "--sortr", "-j", "--threads"}:
                next(args, None)
        else:
            operands.append(arg)
    files = operands if pattern_option else operands[1:]
    return any(_is_global_index(path, cwd, codex_home) for path in files)


def _memory_reads(db: sqlite3.Connection, thread: str, turn: str, columns: set[str], counts: dict, codex_home: Path) -> dict | None:
    if not counts or not {"item_json", "rollout_ordinal"} <= columns:
        return None
    rows = db.execute(
        "SELECT item_type, rollout_ordinal, CASE WHEN item_type='commandExecution' AND json_valid(item_json) "
        "THEN json_extract(item_json, '$.commandActions', '$.cwd') END FROM thread_items "
        "WHERE thread_id=? AND turn_id=? AND item_type IN ('commandExecution','contextCompaction') "
        "ORDER BY rollout_ordinal", (thread, turn),
    )
    commands = early = remaining = 0
    ordered = True
    previous = None
    for kind, ordinal, payload in rows:
        if ordinal is None or ordinal == previous:
            ordered = False
        previous = ordinal
        if kind == "contextCompaction":
            remaining = 2
            continue
        actions, cwd = json.loads(payload) if payload else (None, None)
        if not isinstance(actions, list) or not all(isinstance(a, dict) for a in actions):
            return None
        seen = any(
            a.get("type") == "read" and _is_global_index(a.get("path"), cwd, codex_home)
            or a.get("type") == "search" and _search_reads_index(a, cwd, codex_home)
            for a in actions
        )
        if seen:
            commands += 1
        if remaining:
            if seen:
                early += 1
                remaining = 0
            else:
                remaining -= 1
    return {"global_index_commands": commands, "compactions_with_early_index_read": early if ordered else None}


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
                    "memory_reads": _memory_reads(db, projection, row["turn_id"], item_columns, counts, codex_home),
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
