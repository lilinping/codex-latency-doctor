---
name: codex-latency-doctor
description: 排查 Codex 卡顿、重连、压缩后重读文件或旧图、目标反复续跑。按对话增量复盘，核对耗时证据，修复相应配置或恢复执行进度。
license: MIT
---

# Codex Latency Doctor

Run the bundled CLI with Python 3.11+ on Windows, macOS or Linux. Resolve `<skill-dir>` to the directory containing this `SKILL.md` and use its absolute path. Use `py -3 -B` or `python -B` on Windows and `python3 -B` on macOS and Linux. Check the version before starting. The bundled runtime has no Python dependencies.

Choose the starting point that answers the current question. Run subcommands through the bundled `scripts/doctor.py` launcher.

- For a named slow chat or a recurring review, start with `--json review THREAD_ID`. It returns the latest five closed turns from the local index. Reuse the chat's `next_cursor` as `--after CURSOR` on the next review. Follow `has_more` for the requested interval. An empty successful page completes that check. Open a specific turn only if the counts leave a material question, such as whether a screenshot was regenerated. See [review coverage and examples](references/recovery.md#incremental-chat-review) when needed.
- For reconnects or a machine-wide delay, `audit --hours 24` checks configuration, recognized runtime logs and, on macOS, power state. For cross-chat context pressure, use `tasks --hours 24 --scope interactive --limit 5`. Select the incident window (1–168 hours). Use `--scope all` to include unknown sources or `--scope background` for background work.
- Check log coverage and sample counts. Treat absent observations as unknown. Step-to-HTTP-header timings include local setup and possible retries. Streaming, tool execution and context compaction need their own evidence.
- For chat comparisons, match source and workload, then compare effective threshold, context window, sampled usage and compaction requests. The telemetry does not identify which previous chats, skills or memories contributed to the prompt.

## Reconnecting or compacting repeatedly

- For “Reconnecting 1/5”, note the incident time and match the same turn in the read-only log evidence. Check the retry reasons, HTTP and stream errors, and nearby sleep or wake events. A retry can reflect a broken response stream, a send failure, model capacity or rate limiting. A later HTTP 2xx confirms headers arrived. Check subsequent chat or tool events to establish whether the task resumed.
- If reachability is in question, run `network --samples 3 --compare-direct` through the same launcher. On macOS, add `--compare-macos-proxy` to test the configured fixed system HTTPS proxy explicitly. The unauthenticated probes measure point-in-time reachability. Use a comparable model turn to check streaming stability. Change a route only if the evidence implicates the app's effective route. When failures coincide with sleep, check the running app's idle sleep assertion and distinguish idle sleep from closing the lid or manual sleep.
- For frequent compaction, choose `audit --hours 48` and `tasks --hours 48 --scope all` for a two-day incident. Check effective thresholds on the affected chats, then compare pre-turn and mid-turn requests, compact endpoint HTTP outcomes and paired elapsed times. Several response log rows can belong to the same compaction. Compare the last completed step before compaction with the first meaningful step after it to check continuity. Adjust a threshold only with evidence from comparable long tasks on the same model and context scope.

## Keep progress across turns

- On goal continuation, use the latest user request, the last verified result and the outstanding acceptance condition. Take the action that advances it. Once the requested outcome is verified, deliver it and close the goal when applicable. Another automatic turn alone gives no reason to repeat acceptance.
- At compaction or resume, keep those facts in the existing handoff. Reuse the skill section, code range or image observation it identifies. Read the missing range if the handoff lacks a fact. A new reproduction, a relevant change or an explicit request warrants reopening the affected image. A regenerated screenshot can contain new evidence at the same path.
- For a running job the user asked to monitor, use event waits with the latest cursor, or space out checks until completion. If only a person can supply the missing input or operate an account, finish independent work, state the precise action needed and return control. After handoff, check an uncertain submission or download once, then continue from its actual state. A one-time check ends when its result is known.
- If a chat repeatedly restarts investigation after compaction or resume, use the [recovery harness](references/recovery.md) to preview the native hook, apply it when requested, and verify the first action afterwards. Ordinary latency checks use the commands above.

## Apply, verify, and compare

1. For an authorized change, preview the exact flags with `apply --dry-run`. When it reports `planned`, apply once and record the backup ID. When it reports `unchanged`, move to any pending verification. Use `--compact-tokens` for a measured compaction issue, `--reasoning-effort low|medium|high` for a comparable task experiment, `--disable-mcp NAME` or `--enable-mcp NAME` for a confirmed existing server, and `--prevent-idle-sleep` on macOS for idle sleep. Leave competing editors closed during the write.
2. If the apply result was lost, preview with the same flags and Codex directory. If the preview reports `unchanged`, use `backups` to find IDs matching the current config. Treat these as candidates and confirm which one belongs to the interrupted apply from available evidence. While the outcome or backup remains uncertain, limit follow-up to read-only checks.
3. Once a change is on disk, run `verify` once for that config in a fresh native Codex process. For a desktop comparison, also confirm adoption of the affected setting in a fresh desktop run when available. Preserve active desktop work and report adoption as pending if it cannot be checked yet. If verification fails or desktop adoption is pending, stop the comparison and report what was verified. Use `rollback BACKUP_ID` when the user authorizes restoring a confirmed backup.
4. Reuse comparable evidence where available. When measuring a change, use a representative read-only task or an isolated workload for one before-and-after comparison per change, unless the user requests more trials. Keep real publishes, messages, and automation changes out of the workload. Run `compare BEFORE.json AFTER.json` with saved `--json audit` snapshots for the same Codex directory, equal non-overlapping windows, and similar work. If coverage or samples are insufficient, report the comparison as inconclusive and stop the test.

Pass `--codex-home PATH` and `--json` before the subcommand. Report the evidence, what remains uncertain and the next check in the user's language. Use the sanitized output and keep raw logs, chat transcripts, credentials and configuration backups private.
