"""Inspect the small subset of Codex settings relevant to this diagnosis."""

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
import tomllib
from contextlib import contextmanager
from functools import cache
from pathlib import Path

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID), ("inheritable", wintypes.BOOL),
        ]


COMPACT = "model_auto_compact_token_limit"
SLEEP = "preventSleepWhileRunning"
REASONING = "model_reasoning_effort"
REASONING_EFFORTS = ("low", "medium", "high")
KNOWN_REASONING_EFFORTS = ("none", "minimal", *REASONING_EFFORTS, "xhigh", "max", "ultra")
EXTERNAL_IMPORT = "external-agent-import-sync-enabled"
BACKUPS = "latency-doctor-backups"
ID_PATTERN = re.compile(r"backup-[a-z0-9_]{8}")
MCP_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
HEADER = re.compile(r"(?m)^[ \t]*\[")


class ConfigError(ValueError):
    """A safe-to-display configuration error."""


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _open_regular(path: str | Path, flags: int, mode: int = 0o600) -> int:
    if os.name != "nt":
        return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)

    if flags & os.O_CREAT:
        try:
            return os.open(path, flags | os.O_EXCL, mode)
        except FileExistsError:
            if flags & os.O_EXCL:
                raise
    path = Path(path)
    before = path.lstat()
    if _linked(before) or not stat.S_ISREG(before.st_mode):
        raise OSError("Expected a regular file without reparse points.")
    fd = os.open(path, flags & ~os.O_CREAT, mode)
    try:
        after = path.lstat()
        if _linked(after) or not os.path.samestat(before, after) or not os.path.samestat(before, os.fstat(fd)):
            raise OSError("File changed while opening it.")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read(path: Path) -> bytes:
    with open(path, "rb", opener=_open_regular) as file:
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
            config.get(REASONING) if config.get(REASONING) in KNOWN_REASONING_EFFORTS else None
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


def _set_table_scalar(text: str, table: str, key: str, literal: str, existing: bool, newline: str) -> str:
    headings = list(re.finditer(rf"(?m)^[ \t]*\[{table}\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)", text))
    if len(headings) != 1:
        raise ConfigError("Could not locate a unique supported TOML table for this setting. Edit it manually, then run audit.")
    start = headings[0].end()
    following_header = HEADER.search(text, start)
    end = following_header.start() if following_header else len(text)
    prefix = text[:start]
    if not prefix.endswith("\n"):
        prefix += newline
    return prefix + _set_scalar(text[start:end], key, literal, existing, newline) + text[end:]


def _edited(
    original: bytes,
    compact_tokens: int | None,
    prevent_idle_sleep: bool,
    reasoning_effort: str | None,
    mcp_name: str | None,
    mcp_enabled: bool | None,
) -> tuple[bytes, dict]:
    expected = _parse(original)
    text = original.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    changes = {}
    current_limit = expected.get(COMPACT)
    current_effort = expected.get(REASONING)
    for key, before, after in (
        (COMPACT, current_limit if type(current_limit) is int else None, compact_tokens),
        (REASONING, current_effort if current_effort in KNOWN_REASONING_EFFORTS else None, reasoning_effort),
    ):
        if after is not None and before != after:
            heading = HEADER.search(text)
            end = heading.start() if heading else len(text)
            text = _set_scalar(text[:end], key, json.dumps(after), key in expected, newline) + text[end:]
            expected[key] = after
            changes[key] = {"before": before, "after": after}

    desktop = _table(expected, "desktop")
    sleep_before = desktop.get(SLEEP)
    if prevent_idle_sleep and sleep_before is not True:
        if "desktop" not in expected:
            text += (newline if text.endswith("\n") else newline * 2) + f"[desktop]{newline}{SLEEP} = true{newline}"
        else:
            text = _set_table_scalar(text, "desktop", SLEEP, "true", SLEEP in desktop, newline)
        expected.setdefault("desktop", {})[SLEEP] = True
        changes[f"desktop.{SLEEP}"] = {"before": sleep_before if type(sleep_before) is bool else None, "after": True}

    if mcp_name is not None:
        server = _table(expected, "mcp_servers").get(mcp_name)
        if not isinstance(server, dict):
            raise ConfigError("Expected an existing MCP server in config.toml.")
        before = server.get("enabled", True)
        if before is not mcp_enabled:
            name = re.escape(mcp_name)
            table = rf"""[ \t]*mcp_servers[ \t]*\.[ \t]*(?:{name}|"{name}"|'{name}')[ \t]*"""
            text = _set_table_scalar(text, table, "enabled", json.dumps(mcp_enabled), "enabled" in server, newline)
            server["enabled"] = mcp_enabled
            changes[f"mcp_servers.{mcp_name}.enabled"] = {
                "before": before if type(before) is bool else None, "after": mcp_enabled,
            }

    result = text.encode("utf-8")
    if _parse(result) != expected:
        raise ConfigError("The proposed edit changed additional TOML values. Configuration left unchanged.")
    return result, changes


def _write_private(path: Path, content: bytes) -> None:
    fd = _open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def _replace(path: Path, original: bytes, updated: bytes) -> None:
    if os.name == "nt":
        descriptor = _windows_acl(path, include_owner=True)
    else:
        mode = stat.S_IMODE(path.lstat().st_mode) & 0o777
    fd, temporary = tempfile.mkstemp(prefix=".latency-doctor-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as file:
            if os.name == "nt":
                temporary_descriptor = _windows_acl(temporary_path, include_owner=True)
                if _windows_acl_text(descriptor, _OWNER_SECURITY_INFORMATION) != _windows_acl_text(
                    temporary_descriptor, _OWNER_SECURITY_INFORMATION
                ):
                    raise ConfigError("The replacement would change the config owner. Run as the config owner.")
                # A file can have a more restrictive DACL than the directory it belongs to.
                _set_windows_dacl(temporary_path, descriptor)
            file.write(updated)
            file.flush()
            if os.name != "nt":
                os.fchmod(file.fileno(), mode)
            os.fsync(file.fileno())
        if _read(path) != original:
            raise ConfigError("Configuration changed during the operation. Configuration left unchanged.")
        os.replace(temporary, path)
    finally:
        temporary_path.unlink(missing_ok=True)


_PRIVATE_DACL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)"
_OWNER_SECURITY_INFORMATION = 1
_DACL_SECURITY_INFORMATION = 4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_ALREADY_EXISTS = 183
_SE_FILE_OBJECT = 1
_SDDL_REVISION_1 = 1


@cache
def _windows_function(library: str, name: str, result, *arguments):
    function = getattr(ctypes.WinDLL(library, use_last_error=True), name)
    function.restype = result
    function.argtypes = arguments
    return function


def _windows_acl(path: Path, *, include_owner: bool = False):
    get_security = _windows_function(
        "advapi32", "GetFileSecurityW", wintypes.BOOL,
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD,
    )
    information = _DACL_SECURITY_INFORMATION
    if include_owner:
        information |= _OWNER_SECURITY_INFORMATION
    size = wintypes.DWORD()
    if not get_security(str(path), information, None, 0, ctypes.byref(size)):
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(size.value)
    if not get_security(str(path), information, descriptor, size.value, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return descriptor


def _local_free(pointer) -> None:
    _windows_function("kernel32", "LocalFree", wintypes.HLOCAL, wintypes.HLOCAL)(pointer)


def _windows_acl_text(descriptor, information: int = _DACL_SECURITY_INFORMATION) -> str:
    convert = _windows_function(
        "advapi32", "ConvertSecurityDescriptorToStringSecurityDescriptorW", wintypes.BOOL,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), wintypes.LPDWORD,
    )
    text = wintypes.LPWSTR()
    if not convert(descriptor, _SDDL_REVISION_1, information, ctypes.byref(text), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        _local_free(ctypes.cast(text, wintypes.HLOCAL))


def _set_windows_dacl(path: Path, descriptor) -> None:
    get_dacl = _windows_function(
        "advapi32", "GetSecurityDescriptorDacl", wintypes.BOOL,
        wintypes.LPVOID, wintypes.LPBOOL, ctypes.POINTER(wintypes.LPVOID), wintypes.LPBOOL,
    )
    set_security = _windows_function(
        "advapi32", "SetNamedSecurityInfoW", wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
    )
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    dacl = wintypes.LPVOID()
    if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not present.value:
        raise ConfigError("Could not preserve the existing Windows DACL.")
    inherit = (
        _PROTECTED_DACL_SECURITY_INFORMATION
        if _windows_acl_text(descriptor).startswith("D:P")
        else _UNPROTECTED_DACL_SECURITY_INFORMATION
    )
    error = set_security(str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION | inherit, None, None, dacl, None)
    if error:
        raise ctypes.WinError(error)


@contextmanager
def _windows_descriptor(sddl: str):
    convert = _windows_function(
        "advapi32", "ConvertStringSecurityDescriptorToSecurityDescriptorW", wintypes.BOOL,
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPDWORD,
    )
    descriptor = wintypes.LPVOID()
    if not convert(sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield descriptor
    finally:
        _local_free(descriptor)


def _create_private_windows_directory(path: Path) -> None:
    create_directory = _windows_function(
        "kernel32", "CreateDirectoryW", wintypes.BOOL, wintypes.LPCWSTR, ctypes.POINTER(_SecurityAttributes),
    )
    with _windows_descriptor(_PRIVATE_DACL) as descriptor:
        attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        if not create_directory(str(path), ctypes.byref(attributes)):
            error = ctypes.get_last_error()
            if error != _ERROR_ALREADY_EXISTS:
                raise ctypes.WinError(error)


def _require_private_directory(directory: Path) -> None:
    info = directory.lstat()
    if _linked(info) or not stat.S_ISDIR(info.st_mode):
        raise ConfigError("Expected a private backup directory without links.")
    if os.name == "nt":
        acl = _windows_acl_text(_windows_acl(directory))
        if re.sub(r"^D:P(?:AI|AR)*", "D:P", acl) != _PRIVATE_DACL:
            raise ConfigError("Expected a backup directory with Latency Doctor's restricted Windows ACL.")
    elif info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ConfigError("Expected a private backup directory owned by the current user.")


def _acquire_lock(lock) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise
        raise ConfigError("Another Latency Doctor change is in progress.") from None


@contextmanager
def _locked(codex_home: Path, *, create: bool):
    directory = codex_home / BACKUPS
    if create:
        if os.name == "nt":
            _create_private_windows_directory(directory)
        else:
            directory.mkdir(mode=0o700, exist_ok=True)
    _require_private_directory(directory)
    fd = _open_regular(directory / ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "r+b") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ConfigError("Expected a regular lock file.")
        _acquire_lock(lock)
        try:
            yield directory
        finally:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _digest(content: bytes) -> bytes:
    return hashlib.sha256(content).hexdigest().encode("ascii")


def change_config(
    codex_home: Path,
    *,
    compact_tokens: int | None = None,
    prevent_idle_sleep: bool = False,
    dry_run: bool = False,
    reasoning_effort: str | None = None,
    mcp_name: str | None = None,
    mcp_enabled: bool | None = None,
) -> dict:
    if compact_tokens is not None and (type(compact_tokens) is not int or not 1 <= compact_tokens <= 2**31 - 1):
        raise ConfigError("The compact token threshold must be a positive integer up to 2147483647.")
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
        raise ConfigError("Select low, medium, or high for the reasoning effort.")
    if mcp_name is not None or mcp_enabled is not None:
        if not isinstance(mcp_name, str) or not MCP_NAME_PATTERN.fullmatch(mcp_name) or type(mcp_enabled) is not bool:
            raise ConfigError("Select an MCP server name with ASCII letters, digits, hyphens, or underscores.")
    if compact_tokens is None and not prevent_idle_sleep and reasoning_effort is None and mcp_name is None:
        raise ConfigError("Select at least one setting to change.")
    path = codex_home / "config.toml"
    original = _read(path)
    updated, changes = _edited(original, compact_tokens, prevent_idle_sleep, reasoning_effort, mcp_name, mcp_enabled)
    if original == updated:
        return {"status": "unchanged"}
    if dry_run:
        return {"status": "planned", "changes": changes}
    with _locked(codex_home, create=True) as backups:
        if _read(path) != original:
            raise ConfigError("Configuration changed during the operation. Configuration left unchanged.")
        backup = Path(tempfile.mkdtemp(prefix="backup-", dir=backups))
        if os.name == "nt":
            _set_windows_dacl(backup, _windows_acl(backups))
            _require_private_directory(backup)
        _write_private(backup / "config.toml", original)
        _write_private(backup / "applied.sha256", _digest(updated))
        _replace(path, original, updated)
    return {"status": "applied", "backup_id": backup.name, "desktop_reload_needed": True, "changes": changes}


def restore_config(codex_home: Path, backup_id: str) -> dict:
    if not ID_PATTERN.fullmatch(backup_id):
        raise ConfigError("Invalid backup ID.")
    path = codex_home / "config.toml"
    with _locked(codex_home, create=False) as backups:
        backup = backups / backup_id
        _require_private_directory(backup)
        current = _read(path)
        original = _read(backup / "config.toml")
        _parse(original)
        if current == original:
            return {"status": "unchanged"}
        if _digest(current) != _read(backup / "applied.sha256"):
            raise ConfigError("Configuration has changed since this backup. Use a manual merge to preserve newer edits.")
        _replace(path, current, original)
    return {"status": "restored", "desktop_reload_needed": True}
