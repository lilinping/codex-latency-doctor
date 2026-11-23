# Codex Latency Doctor

[简体中文使用指南](README.zh-CN.md) · [Chinese case study](CASE-STUDY.zh-CN.md) · [Releases](https://github.com/lilinping/codex-latency-doctor/releases)

Diagnose long pauses in Codex with local evidence. Check context compaction, model retries, tool timing, and HTTPS reachability. Preview and apply targeted configuration changes, then verify, compare, or roll them back. On macOS, match sleep and wake events with model connection errors. This independent project uses the MIT license.

## Install

Requires Python 3.11+ on Windows 10/11, macOS, or Linux. Check `py -3 --version` in PowerShell or `python3 --version` on macOS and Linux. If Windows has no `py` launcher, check `python --version` and use `python` in the virtual environment setup below. If you have several versions, you can select one explicitly, for example `py -3.12` or `python3.12`. Open a fresh terminal after installing Python. [Python downloads](https://www.python.org/downloads/)

### Install as a Codex skill

In a Codex chat, ask the built-in skill installer to install the pinned version.

```text
$skill-installer Install https://github.com/lilinping/codex-latency-doctor/tree/v0.6.0/skills/codex-latency-doctor
```

Open a new chat and invoke the installed skill.

```text
$codex-latency-doctor Investigate long pauses over the last 24 hours. Use read-only checks and report the log coverage.
```

The skill bundles the CLI and uses your installed Python with no runtime dependencies. If the skill is missing from the picker, restart Codex after active tasks finish.

To upgrade an existing skill, move the `codex-latency-doctor` folder out of the Codex home's `skills` directory. For example, keep a v0.5.0 copy as `codex-latency-doctor-v0.5.0-backup` directly under the Codex home. Then install v0.6.0 with the command above and open a new chat. The default Codex home is `.codex` under your user home, unless `CODEX_HOME` points elsewhere.

### Install the CLI in Windows PowerShell

Run these commands in a writable directory. Run the later PowerShell examples from the same directory.

For Codex running inside WSL, follow the Linux steps inside that WSL distribution. Use PowerShell for a native Windows Codex installation.

```powershell
py -3 -m venv .venv
$doctorPython = '.\.venv\Scripts\python.exe'
& $doctorPython -m pip install 'https://github.com/lilinping/codex-latency-doctor/releases/download/v0.6.0/codex_latency_doctor-0.6.0-py3-none-any.whl'
& $doctorPython -m codex_latency_doctor --version
& $doctorPython -m codex_latency_doctor audit --hours 24
```

Calling the virtual environment's Python executable directly avoids activating the environment or changing the PowerShell execution policy. In a new PowerShell window, return to the installation directory and set `$doctorPython` again as shown above.

### Install the CLI on macOS and Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'https://github.com/lilinping/codex-latency-doctor/releases/download/v0.6.0/codex_latency_doctor-0.6.0-py3-none-any.whl'
codex-latency-doctor --version
codex-latency-doctor audit --hours 24
```

From a checkout, you can also install with `python -m pip install .` inside an active virtual environment or run the bundled launcher directly, for example `python3 -B skills/codex-latency-doctor/scripts/doctor.py audit --hours 24` on macOS and Linux.

## Diagnose a pause

1. Note roughly when the pause occurred. Run `audit --hours 1` immediately after a recent incident, or choose a window from 1 to 168 hours to cover it. Check the log status, the time of the oldest scanned row and whether the scan was truncated.
2. Compare configured and observed compaction thresholds, then check compaction requests and duration, model retries and errors, and completed tool timings. Step-to-HTTP-header timing includes local preparation and possible retries through the first successful response headers. Streaming and unpaired requests need separate evidence. On macOS, check whether connection errors occurred near sleep or wake.
3. If connectivity is in question, run `network --samples 3 --compare-direct`. The default route uses curl's environment and the direct route bypasses curl's proxy. A system VPN can affect both. An HTTP 401 establishes that the unauthenticated probe received a response. Check model authentication and stream stability independently. If all selected routes fail to get an HTTP response, the command reports `no_http_response` and exits nonzero.

Every command defaults to the current `CODEX_HOME`, or to `.codex` in your user home. Put global options `--codex-home` and `--json` before the subcommand. Substitute the actual directory in the examples.

```powershell
& $doctorPython -m codex_latency_doctor --codex-home 'D:\Codex Data' --json audit --hours 24
& $doctorPython -m codex_latency_doctor network --samples 3 --compare-direct
```

```sh
codex-latency-doctor --codex-home '/path/to/codex-home' --json audit --hours 24
codex-latency-doctor network --samples 3 --compare-direct
```

## Compare chats

Use `tasks` to inspect the context evidence by chat and source. `audit` aggregates all chats in the scanned log window.

If you installed the skill, send this in a Codex chat.

```text
$codex-latency-doctor Compare interactive chats and background tasks over the last 24 hours. Include log coverage, sampled context peaks, compaction thresholds, and automatic compaction requests.
```

```powershell
& $doctorPython -m codex_latency_doctor tasks --hours 24 --scope interactive --limit 10
& $doctorPython -m codex_latency_doctor tasks --hours 24 --scope background --limit 10
```

```sh
codex-latency-doctor tasks --hours 24 --scope interactive --limit 10
codex-latency-doctor tasks --hours 24 --scope background --limit 10
```

| What to check | How to read it |
| --- | --- |
| Coverage and source | Check the scanned period, truncation and matched chat count. `--limit` controls how many chats are displayed. Include unclassified chats with `--scope all`. |
| Auto-compact threshold and full context window | The threshold is a trigger based on the measured context scope. The full window is the model or runtime limit. Check for model or configuration changes when one chat shows several observations. |
| Sampled peak and compaction requests | The peak is the highest observed sample. Only a log entry containing both context use and its threshold contributes to the above-threshold count. Compaction counts reflect recognized requests. Missing samples leave the corresponding measurement unknown. |

Each `task_…` label belongs to one report. Compare similar workloads and carry forward verified decisions with the source artifact or test and the conditions under which you verified it. Context size and compaction evidence cannot identify which memories were used or establish how much faster a chat became.

After context compaction or in a new chat, use the last verified result and continue with the next pending step. Reuse recent evidence while the inputs and target state still match. When following an active task to completion, wait for a state change or new output with a timeout before checking again. During diagnosis, stop when the same state leaves no further check to make.

## Compare before and after a change

1. **Capture a baseline.** Use an existing comparable audit or run a representative read-only or isolated workload once and save an audit JSON file afterward. Record the model, reasoning setting, input, starting context, and tool workload. For jobs that publish, send messages, or change real data, use a dry run or test fixture.
2. **Change one setting.** If you need a config change, back it up using the workflow below and confirm the setting took effect. Run the same safe workload once for the follow-up. Reserve additional runs for an explicit measurement experiment. Use equal-length, non-overlapping observation windows. With the one-hour windows below, take the captures more than one hour apart and complete each workload within its window.
3. **Read the comparison.** Inspect coverage and sample counts before interpreting differences in step-to-header median and long waits, compactions, retries, errors, or tool timing. Track first visible response and total task time separately if those are the user-facing outcomes you want to compare. If the samples are insufficient, record what was observed and what evidence is missing, then end the comparison.

**Windows PowerShell**

```powershell
# Run before the change.
& $doctorPython -m codex_latency_doctor --json audit --hours 1 | Set-Content -LiteralPath .\before.json -Encoding utf8

# Run after the follow-up workload.
& $doctorPython -m codex_latency_doctor --json audit --hours 1 | Set-Content -LiteralPath .\after.json -Encoding utf8
& $doctorPython -m codex_latency_doctor compare .\before.json .\after.json
```

**macOS and Linux**

```sh
# Run before the change.
codex-latency-doctor --json audit --hours 1 > before.json

# Run after the follow-up workload.
codex-latency-doctor --json audit --hours 1 > after.json
codex-latency-doctor compare before.json after.json
```

`compare` reads two local `--json audit` files. Use `--json compare before.json after.json` for scripted output. A delta is the current value minus the baseline value. Deltas need compatible windows, complete scans and observations for the metric. For older audits without a capture time, check overlap separately. Each audit aggregates all chats in its window, so minimize unrelated work during both test periods.

With both reports saved, you can send the following in a Codex chat. Include the full paths if the reports are in another directory.

```text
$codex-latency-doctor Compare before.json and after.json. Check the capture windows, log coverage, and sample counts before explaining which metrics changed.
```

| What changed | What to check before interpreting it |
| --- | --- |
| Step-to-HTTP-header wait | Read the median, maximum and count of waits of at least 30 seconds alongside the number of paired samples. |
| Context compaction | Compare configured and observed thresholds, automatic request counts and compaction timing across similar models and context workloads. |
| Model errors and tool timing | Compare recorded retries, HTTP errors and stream errors. Read completed tool timings alongside the number and type of calls. |

This excerpt uses synthetic logs with one paired sample in each window.

```text
Metric                                         Before        After       Change  Samples (before/after)
Step to HTTP headers median (s)                  45.0         15.0        -30.0  1 / 1
Paired header waits >= 30s                          1            0           -1  1 / 1
```

## Change, verify, and roll back

Choose one setting based on the evidence, then save the baseline from the section above. Use the same Codex home, CLI or desktop installation, and effective model for the follow-up. A reasoning level selected in the chat or passed as a Codex CLI override can take precedence over the default in `config.toml`.

| Evidence | Change to try | Follow-up check |
| --- | --- | --- |
| Frequent costly automatic compactions in comparable chats | `--compact-tokens 120000`. Choose a threshold based on the effective model window and observed context load. The case study confirmed adoption of 120,000. The effect on long-task duration remains unmeasured in a controlled comparison. | Confirm the observed runtime threshold, then compare compaction counts, durations, and task quality. |
| Repeated comparable requests show a long wait, with room to lower reasoning effort | `--reasoning-effort medium` or another level supported by the model. The tool accepts `low`, `medium`, and `high`. | Compare timing and answer quality using the safe workload above. Lowering effort produced mixed timing results in the case study. |
| Startup logs repeatedly identify one MCP server that the workload can run without | `--disable-mcp NAME`. Use the exact name of an existing `[mcp_servers.NAME]` table. The tool accepts letters A–Z, digits, hyphens and underscores in the name. Use `--enable-mcp NAME` to re-enable the server after fixing it. | Check the corresponding startup error and test dependent workflows in isolation. An error disappearing confirms that change alone. |
| macOS idle sleep coincides with active work and connection loss | `--prevent-idle-sleep`. The lid can still trigger sleep. | Reopen the desktop app, then check `power.codex_idle_sleep_assertion` in `audit` during an active task and review sleep events afterward. |

If HTTPS probe results are inconclusive, check actual model requests and streaming stability before changing the proxy. The options above edit Codex settings only.

Close other config writers before applying the change. Preview with `apply --dry-run`. A proposed change shows `status: planned` and the settings to change. The preview leaves the file untouched and creates no backup. Run the same command without `--dry-run` to back up the current configuration under `latency-doctor-backups/` and atomically update `config.toml`. When the result is `status: applied`, keep the returned `backup_id`. If the selected settings already have the requested values, the command reports `status: unchanged`.

**Windows PowerShell**

```powershell
& $doctorPython -m codex_latency_doctor apply --dry-run --compact-tokens 120000
& $doctorPython -m codex_latency_doctor apply --compact-tokens 120000
& $doctorPython -m codex_latency_doctor verify
```

**macOS and Linux**

```sh
codex-latency-doctor apply --dry-run --compact-tokens 120000
codex-latency-doctor apply --compact-tokens 120000
codex-latency-doctor verify
```

With the skill installed, you can request the same action in a Codex chat.

```text
$codex-latency-doctor Preview and apply only the automatic compaction threshold of 120000 in the current Codex home. Verify it and give me the backup ID.
```

After applying a change, run `verify` once. It runs a strict startup check in a new process with the Codex CLI on `PATH`. For desktop measurements, let active tasks finish, reopen the app if needed, and confirm the setting in the app or in telemetry from a new run before comparing. If verification fails or runtime adoption remains unconfirmed, record the result and stop the comparison.

If you decide to roll back after failed verification or a regression, replace `BACKUP_ID` below with the confirmed backup ID. It is usually available in the `apply` result. Rollback refuses to overwrite the current file if it has changed since that backup. Review both versions locally and merge the intended change in that case.

```powershell
& $doctorPython -m codex_latency_doctor rollback BACKUP_ID
& $doctorPython -m codex_latency_doctor verify
```

```sh
codex-latency-doctor rollback BACKUP_ID
codex-latency-doctor verify
```

After restoring, reopen the desktop app when active tasks finish and check the affected workflow with a read-only or isolated task.

### Recover from an interrupted apply

If `apply` was interrupted before returning its result, run `apply --dry-run` with the original flags and Codex home. If it returns `planned`, review the remaining changes against the original request before any further write. If it returns `unchanged`, the requested values are already on disk. Use `backups` to look for the backup ID.

```powershell
& $doctorPython -m codex_latency_doctor --json backups
```

```sh
codex-latency-doctor --json backups
```

With the installed skill, you can also ask in a Codex chat.

```text
$codex-latency-doctor The result of apply --compact-tokens 120000 was lost. Use the same Codex home, check with a dry run, and show matching backup IDs if the setting is already on disk.
```

Pass the same `--codex-home` before `backups` if you used it for `apply`. The read-only command lists `matching_backup_ids` whose saved digest matches the current config. A match establishes file state only. Confirm the backup belongs to the interrupted operation using an independent record, such as the original command output if available. If there are zero or multiple matches, or the candidate is unconfirmed, leave the rollback pending. Run `rollback` only for a confirmed ID when you intend to restore it.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Python is missing or older than 3.11 | Check the interpreter commands in the installation instructions. If an appropriate version is installed, select it explicitly and retry. |
| `missing`, `unavailable`, or `unsupported_schema` in audit logs | Confirm that the tool and your Codex session use the same home directory. Set `--codex-home` for a custom directory. Run a recent task and retry. Log formats can vary by Codex version. |
| No interactive chats in `tasks` | Try `--scope all` to include chats with an unknown source. If it remains empty, check for recognized context telemetry in the selected window. |
| `verify` cannot find or start the Codex CLI | Run `Get-Command codex -All` on Windows or `command -v codex` on macOS and Linux, then check `codex --version`. Install the CLI and make it available on `PATH` if needed. Windows supports a native `codex.exe` or the official npm installation with Node.js. Strict startup verification requires a version that supports the app-server check. |
| `network` cannot find curl | Check `Get-Command curl.exe` in PowerShell or `command -v curl` on macOS and Linux. The probe requires the actual curl executable on `PATH`. |
| `power.status` is `unsupported_platform` | Expect this on Windows and Linux. Log, configuration, and network checks remain available. |

## Data and scope

- `audit` and `tasks` make bounded reads of recognized runtime logs. `tasks` uses chat IDs and source metadata from local state to group evidence. Reports omit raw logs, chat IDs and titles, credentials, config URLs, and project paths. Review workload details before sharing. Local config backups may contain secrets.
- Check missing trust paths before cleanup. An external volume may be unavailable. Project, plugin, agent, and history cleanup stays under the operator's control.

## Test

```sh
PYTHONPATH=skills/codex-latency-doctor/scripts python3 -m unittest discover -s tests -v
```
