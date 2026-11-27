"""Register a recovery hint at the native Codex session boundary."""

import json
from pathlib import Path

from .config import ConfigError, _locked, _read, _replace, _write_private


MATCHER = "compact|resume"
LABEL = "Codex Latency Doctor recovery"
# Fixed shell text only. Hook input, paths and transcript contents never enter the command.
HINT = (
    "Follow the latest user request. Use the current handoff to take the next unfinished action, "
    "or deliver the result if complete. Reuse verified file, skill and image findings. "
    "Read only the missing fact or changed range. Reopen an image for a new reproduction, "
    "a relevant change or an explicit request. Check an uncertain side effect once before retrying."
)
HANDLER = {"type": "command", "command": f'echo "{HINT}"', "timeout": 2, "statusMessage": LABEL}


def _unique_keys(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("Duplicate keys in hooks.json. Resolve them before editing.")
        result[key] = value
    return result


def _load_hooks(path: Path) -> tuple[bytes | None, dict]:
    try:
        original = _read(path)
    except FileNotFoundError:
        return None, {}
    try:
        document = json.loads(original.decode("utf-8-sig"), object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError) as error:
        raise ConfigError("Could not safely parse hooks.json. Check its JSON syntax and keys.") from error
    if not isinstance(document, dict) or not isinstance(document.get("hooks", {}), dict):
        raise ConfigError("Expected an object with a hooks object in hooks.json.")
    return original, document


def recovery_harness(codex_home: Path, action: str, dry_run: bool = False) -> dict:
    if action not in {"status", "install", "remove"}:
        raise ConfigError("Choose status, install or remove for the recovery harness.")
    path = codex_home / "hooks.json"
    original, document = _load_hooks(path)
    groups = document.setdefault("hooks", {}).setdefault("SessionStart", [])
    if not isinstance(groups, list):
        raise ConfigError("Expected a SessionStart array in hooks.json.")
    matches = []
    for index, group in enumerate(groups):
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            raise ConfigError("Expected a handler list in each SessionStart group.")
        for handler in group["hooks"]:
            if not isinstance(handler, dict):
                raise ConfigError("Expected a SessionStart handler object.")
            if handler.get("statusMessage") == LABEL:
                if group != {"matcher": MATCHER, "hooks": [HANDLER]}:
                    raise ConfigError("The recovery hook was customized. Review its SessionStart group in /hooks.")
                matches.append(index)
    result = {
        "registration": "registered" if matches else "absent",
        "event": "SessionStart", "matcher": MATCHER, "handler": HANDLER,
    }
    if action == "status":
        return {"status": "ok", **result}
    if (action == "install") == bool(matches):
        return {"status": "unchanged", **result}
    if dry_run:
        return {"status": "planned", "action": action, **result}

    if action == "install":
        empty_group = {"matcher": MATCHER, "hooks": []}
        if empty_group in groups:
            groups[groups.index(empty_group)]["hooks"].append(HANDLER)
        else:
            groups.append({"matcher": MATCHER, "hooks": [HANDLER]})
    else:
        for index in reversed(matches):
            if index == len(groups) - 1:
                groups.pop()
            else:
                # Codex keys trust by group index. Keep subsequent user hooks at their indices.
                groups[index]["hooks"] = []
    updated = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with _locked(codex_home, create=True):
        if original is None:
            _write_private(path, updated)
        else:
            _replace(path, original, updated)
    return {"status": "applied", **result, "registration": "registered" if action == "install" else "absent"}
