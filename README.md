# Codex Latency Doctor

[简体中文使用指南](README.zh-CN.md)

A local CLI for diagnosing long pauses in Codex. Compare configuration with observed context usage, match macOS sleep events with model connection errors, and measure HTTPS reachability. This independent project uses the MIT license.

For the original investigation and what each cleanup achieved, see the [Chinese case study](CASE-STUDY.zh-CN.md).

## Install as a Codex skill

Ask Codex to install the skill from the pinned release:

```text
$skill-installer Install https://github.com/lilinping/codex-latency-doctor/tree/v0.3.0/skills/codex-latency-doctor
```

After installation, open a new Codex chat and run the skill:

```text
$codex-latency-doctor Investigate long pauses in Codex over the last 24 hours.
```

If the skill is missing from the picker, restart Codex. The installed skill bundles the CLI and uses an existing Python 3.11+ interpreter. It runs local diagnostics without pip or a runtime download.

## Quick start

Requires Python 3.11+ on macOS or Linux. If `python3 --version` is older, use an installed newer interpreter such as `python3.12`. The package has no runtime Python dependencies. Power diagnostics require macOS, `network` uses curl, and `verify` uses Codex on your PATH.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'https://github.com/lilinping/codex-latency-doctor/releases/download/v0.3.0/codex_latency_doctor-0.3.0-py3-none-any.whl'
codex-latency-doctor audit --hours 24
codex-latency-doctor tasks --scope interactive --limit 5
```

You can install from a checkout with `python -m pip install .` inside the virtual environment, or run directly from the source directory:

```sh
python3 -B skills/codex-latency-doctor/scripts/doctor.py audit --hours 24
```

## Compare chats

```sh
codex-latency-doctor tasks --hours 24 --scope interactive --limit 10
codex-latency-doctor --json tasks --hours 24 --scope background
```

- **Check coverage first.** The report counts chats with recognized context telemetry in the bounded log window. The display limit controls how many are shown. Chat source comes from the local state database when available. Unknown sources stay visible in an unfiltered report. Each `task_…` label belongs to one report.
- **Compare like workloads.** Use interactive chats for desktop or CLI behavior. Background workers and approval reviewers can use different models and policies. Within a chat, model selections and context limits can change. Multiple observed values indicate that a chat contains different settings over the sampled window.
- **Read the columns together.** The auto-compact threshold is a trigger based on the context scope in effect. The full context window is a model/runtime limit. Peak context use is the largest recognized sample. A compaction count measures recorded automatic requests. Incomplete logs can leave any of these unknown, and a measured peak may miss activity between samples.
- **Carry evidence into the next task.** Reference the verified decision, source artifact or test, and the conditions under which it was verified. Check that the reference still applies to the current project, version, and workload. The diagnostic report provides comparable observations and has no mechanism to inject context into a chat. Comparing settings and token use cannot establish which memories were read or how much speed changed.

## Diagnose, change, verify

1. **Check a representative incident window.** Compare configured and observed compact thresholds. Check for connection errors near OS wakes. Step-to-header measurements include local preparation and any retries up to the first successful model HTTP headers. They exclude streaming and unpaired requests. Compare these observations separately from completed tool durations. A bounded scan may cover only part of the window.
2. **Compare network routes if connectivity is in question.** The default route uses curl's inherited proxy environment. Export the proxy in `HTTPS_PROXY` or `ALL_PROXY` if the app uses a proxy that the shell does not inherit. The direct route bypasses curl's proxy. A system VPN can still affect both routes. An HTTP 401 means the endpoint responded to the unauthenticated probe. Check the results for each route. If no selected route produces an HTTP response, the command returns `no_http_response` and exits nonzero.
3. **Apply changes supported by the evidence.** Enable idle sleep prevention when macOS idle sleep interrupts work. Choose a compact threshold after measuring repeated compaction and checking the model's effective context limit. The case study's 120,000 value was specific to its model and workload.
4. **Verify with a fresh process and comparable workloads.** When active tasks finish, relaunch the desktop. Audit during a new long task to observe the effective threshold and idle-sleep assertion. Compare compaction, first useful reply and completion times across similar workloads. A short model request verifies basic operation.

```sh
codex-latency-doctor --json audit --hours 24
codex-latency-doctor network --samples 3 --compare-direct
codex-latency-doctor verify
```

The following is an example using both case-study settings. The flags work independently. Close the settings editor before applying to avoid concurrent config writes.

```sh
codex-latency-doctor apply --compact-tokens 120000 --prevent-idle-sleep
codex-latency-doctor verify
# Replace the placeholder with the ID printed by apply.
codex-latency-doctor rollback BACKUP_ID
```

Put the global options `--codex-home /path/to/codex-home` and `--json` before the command. The default directory is the current `CODEX_HOME`, or `~/.codex`. The startup check tests the selected native CLI with that directory, using its defaults if there is no configuration file. Audit a running desktop task to verify active settings. The optional smoke test below sends a real model request using your existing Codex login. It can load the applicable Codex instructions and memory. The ephemeral flag controls session file persistence.

```sh
codex exec --ephemeral --skip-git-repo-check --sandbox read-only 'Reply with exactly OK. Do not use tools.'
```

## Data and scope

- `audit` summarizes all chats covered by the selected Codex home's bounded runtime log window, alongside local configuration and macOS power diagnostics. Use `tasks` to inspect the context evidence by chat and source.
- Both commands parse bounded snippets of recognized runtime log entries. `tasks` also reads chat IDs and source metadata from the local state database for grouping and classification. Reports omit raw logs, chat IDs and titles, credentials, configuration URLs, and project paths. Review numeric workload details before sharing a report. Log formats vary by release, so check coverage before drawing conclusions.
- `apply` writes only the selected values to `config.toml` and stores a private copy in `latency-doctor-backups/`. This copy can contain credentials and should stay local. File replacement is atomic. Rollback checks that the current configuration still matches the applied version. Close the Codex settings editor or any other configuration writer before applying or rolling back.
- Review missing trust paths before cleanup. An external volume may be unavailable. Project, plugin, agent, and history cleanup stays under the operator's control.
- Idle sleep prevention protects active work from idle system sleep. macOS handles closing a laptop lid separately.

## Test

```sh
PYTHONPATH=skills/codex-latency-doctor/scripts python3 -m unittest discover -s tests -v
```
