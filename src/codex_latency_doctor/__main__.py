"""Command line entry point."""

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .checks import probe_network, verify_native
from .config import ConfigError, change_config, inspect_config, restore_config
from .diagnostics import collect_diagnostics, collect_tasks


def _bounded(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("Expected an integer.") from None
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"Expected an integer from {minimum} to {maximum}.")
        return number
    return parse


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="codex-latency-doctor",
        description="Diagnose Codex delays locally. Writes and network checks require explicit commands.",
    )
    parser.add_argument("--codex-home", type=Path, help="Directory containing Codex config, logs, and local state.")
    parser.add_argument("--json", action="store_true", help="Output structured JSON without raw log text.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="Read configuration, recent logs, and macOS power state.")
    audit.add_argument("--hours", type=_bounded(1, 168), default=24)

    tasks = commands.add_parser("tasks", help="Compare observed context usage across Codex chats.")
    tasks.add_argument("--hours", type=_bounded(1, 168), default=24)
    tasks.add_argument("--limit", type=_bounded(1, 100), default=10, help="Maximum chats to display (default: 10).")
    tasks.add_argument("--scope", choices=("all", "interactive", "background", "unknown"), default="all",
                       help="Filter by chat source. Counts include all observed chats.")

    network = commands.add_parser("network", help="Measure unauthenticated HTTPS reachability.")
    network.add_argument("--service", choices=("chatgpt", "api"), default="chatgpt")
    network.add_argument("--samples", type=_bounded(1, 5), default=3)
    network.add_argument("--compare-direct", action="store_true", help="Also probe with curl's proxy bypassed.")

    commands.add_parser("verify", help="Check startup with the native Codex CLI on PATH.")
    apply = commands.add_parser("apply", help="Back up config.toml and apply selected settings.")
    apply.add_argument("--compact-tokens", type=_bounded(1, 2**31 - 1))
    apply.add_argument("--prevent-idle-sleep", action="store_true")
    restore = commands.add_parser("rollback", help="Restore an unchanged applied configuration from its backup.")
    restore.add_argument("backup_id")
    return parser.parse_args(argv)


def _print_report(report: dict, level: int = 0) -> None:
    prefix = "  " * level
    for key, value in report.items():
        label = key.replace("_", " ")
        if isinstance(value, dict):
            print(prefix + label)
            _print_report(value, level + 1)
        else:
            print(f"{prefix}{label:.<36} {json.dumps(value, ensure_ascii=False)}")


def _print_tasks(report: dict) -> None:
    print(f"Chat context observations | last {report['window_hours']}h | {report['filter']}")
    if report["status"] != "ok":
        reason = report["status"].replace("_", " ")
        print(f"Runtime logs are {reason}.")
        return

    scopes = ", ".join(f"{count} {scope}" for scope, count in report["threads_by_scope"].items())
    print(f"Observed chats: {scopes}. Showing {len(report['tasks'])} of {report['threads_matched']} matching.")
    if report["classification_status"] != "ok":
        reason = report["classification_status"].replace("_", " ")
        print(f"Chat source metadata is {reason}. Use --scope all to include unclassified chats.")
    oldest_age = report["oldest_row_age_seconds"]
    coverage = f" Oldest scanned row was {oldest_age // 60:,} minutes ago." if oldest_age is not None else ""
    print(f"Scanned {report['rows_scanned']:,} log rows.{coverage}")
    if report["truncated"]:
        print("The log scan was truncated. Counts cover the rows scanned.")
    if report["unattributed_observations"]:
        print(f"Observations without a chat ID: {report['unattributed_observations']:,}.")
    if not report["tasks"]:
        print("No matching chats with recognized context telemetry.")
        return

    for task in report["tasks"]:
        thresholds = " / ".join(f"{int(value):,}" for value in task["effective_threshold_observations"]) or "unknown"
        windows = " / ".join(f"{int(value):,}" for value in task["full_context_window_observations"]) or "unknown"
        tokens = task["context_scope_tokens"]
        peak = f"{tokens['max']:,}" if tokens["max"] is not None else "unknown"
        requests = sum(task["compaction_requests"].values())
        print(f"\n{task['task']}  {task['classification']}")
        print(f"  Auto-compact threshold {thresholds} | full context window {windows}")
        print(f"  Peak sampled context {peak} | usage samples {tokens['samples']} | recorded automatic requests {requests}")
        paired = tokens["paired_threshold_samples"]
        if paired:
            print(f"  Paired threshold samples {paired} | above threshold {tokens['above_threshold_samples']}")
        if task["model_status"] == "mixed" or task["context_scope_status"] == "mixed":
            print("  Observations span more than one model or context scope.")
        if task["threshold_status"] == "partial":
            print("  Some context observations omit the auto-compact threshold.")


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    codex_home = args.codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    codex_home = codex_home.expanduser()
    exit_code = 0
    try:
        match args.command:
            case "audit":
                result = {"config": inspect_config(codex_home), **collect_diagnostics(codex_home, args.hours)}
            case "tasks":
                result = collect_tasks(codex_home, args.hours, args.limit, scope=args.scope)
                if result["status"] != "ok":
                    exit_code = 1
            case "network":
                result = probe_network(args.service, args.samples, compare_direct=args.compare_direct)
                if result["status"] != "ok":
                    exit_code = 1
            case "verify":
                result = verify_native(codex_home)
                if result["status"] != "accepted":
                    exit_code = 1
            case "apply":
                result = change_config(codex_home, compact_tokens=args.compact_tokens, prevent_idle_sleep=args.prevent_idle_sleep)
            case "rollback":
                result = restore_config(codex_home, args.backup_id)
    except ConfigError as error:
        result, exit_code = {"status": "error", "message": str(error)}, 1
    except OSError:
        result, exit_code = {"status": "error", "message": "Could not access the requested files. Check ownership, permissions, and file type."}, 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.command == "tasks" and "window_hours" in result:
        _print_tasks(result)
    else:
        _print_report(result)
        if result.get("desktop_reload_needed"):
            print("After active tasks finish, reopen the desktop app and audit a new run.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
