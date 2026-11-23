---
name: codex-latency-doctor
description: Diagnose long pauses in a local Codex desktop or CLI session using runtime timings, context compaction, chat comparisons, macOS sleep evidence, and optional HTTPS probes. Use for Codex 卡顿、长时间无响应、上下文压缩 or suspected Codex connectivity problems.
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
- For chat comparisons, match source and workload, then compare effective threshold, context window, sampled usage and compaction requests. Carry forward verified decisions and their source artifacts. The telemetry does not identify which previous chats, skills or memories contributed to the prompt.
- For a before-and-after comparison, use `compare BEFORE.json AFTER.json` through the same launcher. Both inputs must be saved `--json audit` results. Compare the same Codex directory across equal, separate time windows and similar work. Read sample counts, coverage and any limitations alongside the changes.
- For a connectivity investigation, run `network --samples 3 --compare-direct` through the same launcher. This makes unauthenticated HTTPS requests using curl. A response establishes reachability at the time of the probe. Curl's inherited route can differ from the app's route.
- If the user authorizes a configuration change, use `apply` with only the settings justified by the evidence and record the returned backup ID. The idle-sleep option and power evidence are macOS only. Close competing writers before applying or rolling back. Run `verify` to check a fresh native Codex process. Check desktop adoption in a new desktop run and assess speed with comparable work. Use `rollback BACKUP_ID` when the user requests recovery.

Pass `--codex-home PATH` and `--json` before the subcommand. Report the evidence, what remains uncertain and the next check in the user's language. Use the sanitized output and keep raw logs, chat transcripts, credentials and configuration backups private.
