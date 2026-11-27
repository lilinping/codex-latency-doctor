# Recovery after compaction or resume

Use the handoff already present in the chat. For example, “The card route passed on build A. The current request concerns the course page. The next action is its pending keyboard check.” An old card screenshot supplies evidence for the card route. A new report about that route can reopen it. If a publish was interrupted after submission, check the receipt before deciding whether to retry.

## Enable the native reminder

Run these commands with the installed skill launcher (`python3 -B "<skill-dir>/scripts/doctor.py"` on macOS or Linux, `py -3 -B "<skill-dir>/scripts/doctor.py"` on Windows) or the installed CLI.

```sh
codex-latency-doctor harness install --dry-run
codex-latency-doctor harness install
codex-latency-doctor harness status
codex-latency-doctor harness remove
```

Installation appends one `SessionStart` group in the selected Codex home's `hooks.json`. It matches `compact` and `resume`. Its shell command prints fixed text, with a two-second timeout. It reads no transcript, project file, image or hook input and calls no model. Fresh starts and ordinary tool calls incur no handler execution. The handoff and evidence stay in their original chat.

`status` checks file registration. To review trust, locate **Codex Latency Doctor recovery** under the **SessionStart** event. Its matcher is `compact|resume` and its source is the selected Codex home's `hooks.json`.

- On desktop, open **Settings → Hooks → From Config → User config**, expand **SessionStart**, and choose **Trust** on that row. Use **Reload hooks** if the listing is stale.
- In the terminal, start `codex`. Choose **Review hooks** at startup or enter `/hooks` at the Codex input prompt. Select **SessionStart** and press Enter. Select the recovery entry and press `t` when the detail view shows `t trust`. At the event overview the same key applies to all hooks.

`remove` withdraws the registration and preserves other handlers. On a host without this event, apply the handoff rule directly.

## Check the outcome

On a comparable resumed chat, look for the completed recovery hook and the first substantive action. Count an unchanged skill reread or an old image reopened without a new reason separately from a new code range or an affected acceptance check. For a long silent tool batch, distinguish wall time from command runtime before attributing the delay. Registration, delivery of the hint and an actual reduction in repeat work are separate observations.

## Incremental chat review

Use the thread ID from the affected chat link. Use the last cursor for that same chat from the existing review or automation handoff.

```sh
codex-latency-doctor --json review THREAD_ID
codex-latency-doctor --json review THREAD_ID --after CURSOR
```

The first call returns the most recent closed turns in completion order. `older_turns_omitted` marks an initial sample. With a cursor, pages start after the previous completion, including a previously open turn that finished later. Consume each page before saving its `next_cursor`. Continue while `has_more` is true; an empty page keeps the cursor. The default page size is five and `--limit` accepts 1–25.

The query reads only local index metadata. When the state index points a sidebar chat at a continuation segment, `coverage: current_segment` identifies that scope. Older segments are outside that page. `direct_index` covers the selected ID in the turn index. A segment change invalidates an earlier cursor. `duration_ms` is client-recorded time. Event counts cover compaction, shell commands, image views and explicit sleep items. A null duration or null event summary means the corresponding measurement is missing. A count of image views alone cannot establish that an image was stale. Identify a suspected repeat in that turn only when it affects the conclusion.

`not_indexed`, `missing` and `unsupported_schema` indicate unavailable coverage. `invalid_cursor` means the chat and its previous position could not be matched. Report the gap before taking a fresh sample. A new baseline can omit intervening completions. The tool keeps no cursor file and reads no transcript or project media.

## Design sources

- Anthropic's long-running harness passes verified progress between sessions and works on one outstanding feature at a time. This skill uses the existing handoff as that progress record. [Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)
- LangGraph scopes short-term checkpoints to a thread. Recovery here takes its evidence from the current chat. [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- Aider selects repository context by relevance and a token budget. Here the next action determines which missing file range to read. [Repository map](https://aider.chat/docs/repomap.html)
- The native Codex handler matches a session source and can deliver a successful command's output as model context. [Codex SessionStart implementation](https://github.com/openai/codex/blob/rust-v0.171.0/codex-rs/hooks/src/events/session_start.rs)
