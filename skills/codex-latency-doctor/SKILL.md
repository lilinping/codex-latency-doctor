---
name: codex-latency-doctor
description: Diagnose Codex 卡顿、长时间无响应 or context compaction with local logs and before/after comparisons. Preview, apply and roll back targeted Codex performance settings, including compaction, reasoning effort, individual MCP registrations and macOS idle sleep.
license: MIT
---

# Codex Latency Doctor

Run the bundled CLI with Python 3.11+ on Windows, macOS or Linux. Resolve `<skill-dir>` to the directory containing this `SKILL.md` and use its absolute path. Use `py -3 -B` or `python -B` on Windows and `python3 -B` on macOS and Linux. Check the version before starting. The bundled runtime has no Python dependencies.

Start with the commands that match the user's incident. Use the requested time window (1–168 hours) or the default 24 hours.

```sh
python3 -B "<skill-dir>/scripts/doctor.py" --json audit --hours 24
python3 -B "<skill-dir>/scripts/doctor.py" tasks --hours 24 --scope interactive --limit 5
```

- `audit` reads local configuration, recognized runtime logs and, on macOS, power state. `tasks` compares anonymous context measurements. Use `--scope all` to include unknown sources or `--scope background` to focus on background work.
- Check log coverage and sample counts. Treat absent observations as unknown. Step-to-HTTP-header timings include local setup and possible retries. Streaming, tool execution and context compaction need their own evidence.
- For chat comparisons, match source and workload, then compare effective threshold, context window, sampled usage and compaction requests. The telemetry does not identify which previous chats, skills or memories contributed to the prompt.
- For a connectivity investigation, run `network --samples 3 --compare-direct` through the same launcher. This makes unauthenticated HTTPS requests using curl. A response establishes reachability at the time of the probe. Curl's inherited route can differ from the app's route.

## Keep progress across turns

- Reuse a recent result while its inputs and the relevant state still match. Wait for a relevant change before requesting the same status again. When asked to monitor through completion, use bounded event waits with the latest cursor when available, or space out status checks. Continue waiting after an unchanged result or timeout. For a one-time diagnosis, report an unchanged or inconclusive state and end the check.
- After compaction, interruption, or a new chat, carry forward the last verified step, its evidence, and the next unfinished step. For an action with side effects and an unknown outcome, check the resulting state once before deciding whether a retry is safe.

## Apply, verify, and compare

1. For an authorized change, preview the exact flags with `apply --dry-run`. When it reports `planned`, apply once and record the backup ID. When it reports `unchanged`, move to any pending verification. Use `--compact-tokens` for a measured compaction issue, `--reasoning-effort low|medium|high` for a comparable task experiment, `--disable-mcp NAME` or `--enable-mcp NAME` for a confirmed existing server, and `--prevent-idle-sleep` on macOS for idle sleep. Leave competing editors closed during the write.
2. If the apply result was lost, preview with the same flags and Codex directory. If the preview reports `unchanged`, use `backups` to find IDs matching the current config. Treat these as candidates and confirm which one belongs to the interrupted apply from available evidence. While the outcome or backup remains uncertain, limit follow-up to read-only checks.
3. Once a change is on disk, run `verify` once for that config in a fresh native Codex process. For a desktop comparison, also confirm adoption of the affected setting in a fresh desktop run when available. Preserve active desktop work and report adoption as pending if it cannot be checked yet. If verification fails or desktop adoption is pending, stop the comparison and report what was verified. Use `rollback BACKUP_ID` when the user authorizes restoring a confirmed backup.
4. Reuse comparable evidence where available. When measuring a change, use a representative read-only task or an isolated workload for one before-and-after comparison per change, unless the user requests more trials. Keep real publishes, messages, and automation changes out of the workload. Run `compare BEFORE.json AFTER.json` with saved `--json audit` snapshots for the same Codex directory, equal non-overlapping windows, and similar work. If coverage or samples are insufficient, report the comparison as inconclusive and stop the test.

Pass `--codex-home PATH` and `--json` before the subcommand. Report the evidence, what remains uncertain and the next check in the user's language. Use the sanitized output and keep raw logs, chat transcripts, credentials and configuration backups private.
