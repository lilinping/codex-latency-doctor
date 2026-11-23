"""Inspect the small subset of Codex settings relevant to this diagnosis."""

import hashlib
import os
import re
import stat
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path


COMPACT = "model_auto_compact_token_limit"
SLEEP = "preventSleepWhileRunning"
EXTERNAL_IMPORT = "external-agent-import-sync-enabled"
BACKUPS = "latency-doctor-backups"
ID_PATTERN = re.compile(r"backup-[a-z0-9_]{8}")
HEADER = re.compile(r"(?m)^[ \t]*\[")


class ConfigError(ValueError):
    """A safe-to-display configuration error."""


def _read(path: Path) -> bytes:
    with open(path, "rb", opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW | os.O_NONBLOCK)) as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise ConfigError("Expected a regular config or backup file of at most 2 MiB.")
        return file.read()


def _parse(contents: bytes) -> dict:
    try:
        return tomllib.loads(contents.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError):
        raise ConfigError("Could not parse the file as UTF-8 TOML.") from None


def _table(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _stale_trusts(projects: dict) -> int:
    missing = 0
    for name, entry in projects.items():
        if not Path(name).is_absolute() or not isinstance(entry, dict) or entry.get("trust_level") != "trusted":
            continue
        try:
            Path(name).stat()
        except FileNotFoundError:
            missing += 1
        except (OSError, ValueError):
            continue
    return missing


def inspect_config(codex_home: Path) -> dict:
    try:
        config = _parse(_read(codex_home / "config.toml"))
    except FileNotFoundError:
        return {"status": "missing"}
    except (ConfigError, OSError):
        return {"status": "unreadable"}

    desktop = _table(config, "desktop")
    providers = _table(config, "model_providers")
    provider = config.get("model_provider")
    return {
        "status": "ok",
        "compact_tokens": config.get(COMPACT) if type(config.get(COMPACT)) is int else None,
        "prevent_idle_sleep": desktop.get(SLEEP) if type(desktop.get(SLEEP)) is bool else None,
        "external_import_enabled": (
            desktop.get(EXTERNAL_IMPORT) if type(desktop.get(EXTERNAL_IMPORT)) is bool else None
        ),
        "mcp_count": len(_table(config, "mcp_servers")),
        "custom_provider_count": len(providers),
        "selected_custom_provider": isinstance(provider, str) and provider in providers,
        "reasoning_effort": (
            config.get("model_reasoning_effort")
            if config.get("model_reasoning_effort") in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
            else None
        ),
        "missing_trusted_project_paths": _stale_trusts(_table(config, "projects")),
    }


def _set_scalar(section: str, key: str, literal: str, existing: bool, newline: str) -> str:
    pattern = re.compile(rf"(?m)^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)[^\s#]+([ \t]*(?:#[^\r\n]*)?)(?=\r?$)")
    if existing:
        updated, count = pattern.subn(lambda match: match[1] + literal + match[2], section)
        if count != 1:
            raise ConfigError("This key uses an unsupported TOML layout. Edit it manually, then run audit.")
        return updated
    return f"{key} = {literal}{newline}" + section


def _edited(original: bytes, compact_tokens: int | None, prevent_idle_sleep: bool) -> bytes:
    expected = _parse(original)
    text = original.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    current_limit = expected.get(COMPACT)
    if compact_tokens is not None and (type(current_limit) is not int or current_limit != compact_tokens):
        heading = HEADER.search(text)
        end = heading.start() if heading else len(text)
        text = _set_scalar(text[:end], COMPACT, str(compact_tokens), COMPACT in expected, newline) + text[end:]
        expected[COMPACT] = compact_tokens

    if prevent_idle_sleep and _table(expected, "desktop").get(SLEEP) is not True:
        headings = list(re.finditer(r"(?m)^[ \t]*\[desktop\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)", text))
        if len(headings) > 1:
            raise ConfigError("Could not locate a unique desktop section.")
        if headings:
            start = headings[0].end()
            following_header = HEADER.search(text, start)
            end = following_header.start() if following_header else len(text)
            prefix = text[:start]
            if not prefix.endswith("\n"):
                prefix += newline
            text = prefix + _set_scalar(text[start:end], SLEEP, "true", SLEEP in _table(expected, "desktop"), newline) + text[end:]
        elif "desktop" not in expected:
            text += (newline if text.endswith("\n") else newline * 2) + f"[desktop]{newline}{SLEEP} = true{newline}"
        else:
            raise ConfigError("The desktop section uses an unsupported TOML layout. Edit it manually, then run audit.")
        expected.setdefault("desktop", {})[SLEEP] = True

    result = text.encode("utf-8")
    if _parse(result) != expected:
        raise ConfigError("The proposed edit changed additional TOML values. Configuration left unchanged.")
    return result


def _write_private(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def _replace(path: Path, original: bytes, updated: bytes) -> None:
    mode = stat.S_IMODE(path.lstat().st_mode) & 0o777
    fd, temporary = tempfile.mkstemp(prefix=".latency-doctor-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(updated)
            file.flush()
            os.fchmod(file.fileno(), mode)
            os.fsync(file.fileno())
        if _read(path) != original:
            raise ConfigError("Configuration changed during the operation. Configuration left unchanged.")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _locked(codex_home: Path, *, create: bool):
    import fcntl

    directory = codex_home / BACKUPS
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ConfigError("Expected a private backup directory owned by the current user.")
    fd = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "rb") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ConfigError("Expected a regular lock file.")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigError("Another Latency Doctor change is in progress.") from None
        yield directory


def _digest(content: bytes) -> bytes:
    return hashlib.sha256(content).hexdigest().encode("ascii")


def change_config(codex_home: Path, *, compact_tokens: int | None, prevent_idle_sleep: bool) -> dict:
    if compact_tokens is not None and (type(compact_tokens) is not int or not 1 <= compact_tokens <= 2**31 - 1):
        raise ConfigError("The compact token threshold must be a positive integer up to 2147483647.")
    if compact_tokens is None and not prevent_idle_sleep:
        raise ConfigError("Select at least one setting to change.")
    path = codex_home / "config.toml"
    original = _read(path)
    updated = _edited(original, compact_tokens, prevent_idle_sleep)
    if original == updated:
        return {"status": "unchanged"}
    with _locked(codex_home, create=True) as backups:
        if _read(path) != original:
            raise ConfigError("Configuration changed during the operation. Configuration left unchanged.")
        backup = Path(tempfile.mkdtemp(prefix="backup-", dir=backups))
        _write_private(backup / "config.toml", original)
        _write_private(backup / "applied.sha256", _digest(updated))
        _replace(path, original, updated)
    return {"status": "applied", "backup_id": backup.name, "desktop_reload_needed": True}


def restore_config(codex_home: Path, backup_id: str) -> dict:
    if not ID_PATTERN.fullmatch(backup_id):
        raise ConfigError("Invalid backup ID.")
    path = codex_home / "config.toml"
    with _locked(codex_home, create=False) as backups:
        backup = backups / backup_id
        info = backup.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ConfigError("Expected a private backup owned by the current user.")
        current = _read(path)
        original = _read(backup / "config.toml")
        _parse(original)
        if current == original:
            return {"status": "unchanged"}
        if _digest(current) != _read(backup / "applied.sha256"):
            raise ConfigError("Configuration has changed since this backup. Use a manual merge to preserve newer edits.")
        _replace(path, current, original)
    return {"status": "restored", "desktop_reload_needed": True}
