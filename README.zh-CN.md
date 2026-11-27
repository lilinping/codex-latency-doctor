# Codex Latency Doctor 中文指南

Codex 长时间没有可见进度时，用这个本地工具查看上下文压缩、模型请求重试、工具耗时和 HTTPS 连通性。你可以预览并应用有依据的配置修改，然后验证、对比或回滚。macOS 还可以检查睡眠和唤醒事件。项目采用 MIT 许可证。

[English README](README.md) · [真实排查案例](CASE-STUDY.zh-CN.md) · [发布版本](https://github.com/lilinping/codex-latency-doctor/releases)

## 安装教程

下方固定版本的命令安装稳定版 v0.6.0。要使用 `main` 上新增的重连诊断、压缩统计和旧截图续跑说明，可把技能安装地址中的 `v0.6.0` 换成 `main`。使用 CLI 时，在 `main` 的仓库目录运行 `python -m pip install .`。

支持 Windows 10/11、macOS 和 Linux。需要 Python 3.11 或以上版本。先核对版本。

- **Windows PowerShell** 运行 `py -3 --version`。如果没有 `py` 启动器，运行 `python --version`，确认版本后将安装命令中的 `py -3` 换成 `python`。
- **macOS 和 Linux** 运行 `python3 --version`。如果系统解释器较旧，使用已安装的新版本，例如 `python3.12`。

如果还没有符合要求的解释器，安装后重新打开终端。[Python 官方下载](https://www.python.org/downloads/)

### 在 Codex 中安装技能

在 Codex 聊天中发送：

```text
$skill-installer 请从 https://github.com/lilinping/codex-latency-doctor/tree/v0.6.0/skills/codex-latency-doctor 安装技能
```

安装后新建聊天并运行：

```text
$codex-latency-doctor 只读排查最近 24 小时的卡顿，给出日志覆盖情况、可能卡在哪一步，以及下一项验证。
```

技能已包含诊断程序。安装后使用现有的 Python 运行本地检查，无需 pip。技能列表还未显示此技能时，等活动任务结束后重启 Codex。

如果已安装旧版，把 `codex-latency-doctor` 文件夹移出 Codex 目录下的 `skills` 目录做备份。例如，可以把 v0.5.0 的备份放在 Codex 目录下，命名为 `codex-latency-doctor-v0.5.0-backup`。然后用上面的指令安装 v0.6.0 并新建聊天。默认 Codex 目录是用户主目录下的 `.codex`，自定义位置以 `CODEX_HOME` 为准。

### Windows PowerShell 安装 CLI

在自己有写权限的目录中执行以下命令。后续 PowerShell 示例也从这个目录运行。

如果 Codex 运行在 WSL 中，请在该 WSL 发行版里按 Linux 步骤安装和诊断。Windows 原生 Codex 在 PowerShell 中诊断。

```powershell
py -3 -m venv .venv
$doctorPython = '.\.venv\Scripts\python.exe'
& $doctorPython -m pip install 'https://github.com/lilinping/codex-latency-doctor/releases/download/v0.6.0/codex_latency_doctor-0.6.0-py3-none-any.whl'
& $doctorPython -m codex_latency_doctor --version
& $doctorPython -m codex_latency_doctor audit --hours 24
```

直接调用虚拟环境中的 `python.exe` 无需激活虚拟环境，也无需修改 PowerShell 执行策略。新开 PowerShell 窗口时，先回到安装目录并重新设置上面的 `$doctorPython`。

### macOS 和 Linux 安装 CLI

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'https://github.com/lilinping/codex-latency-doctor/releases/download/v0.6.0/codex_latency_doctor-0.6.0-py3-none-any.whl'
codex-latency-doctor --version
codex-latency-doctor audit --hours 24
```

## 使用教程

### 找出一次卡顿可能发生在哪一步

1. 记录卡顿的大致时间。立即执行 `audit --hours 1`，或者用 `--hours` 选择覆盖问题时段的范围，取值 1 至 168 小时。先看 `logs.status`、最旧的扫描记录和 `truncated`。如果实际数据覆盖不到该时段，后续只能给出有限结论。
2. 对照配置值、运行时压缩阈值、自动压缩次数及耗时，再看模型重试和错误。模型步骤到 HTTP 响应头的计时包含本地准备和可能的重试，截止于首次成功收到响应头，后续流式输出需另查。已结束的工具调用有单独的计时。
3. 怀疑网络时运行 `network --samples 3 --compare-direct`。默认线路使用 curl 继承的代理环境，对照线路绕过 curl 代理。两者仍可能受到系统 VPN 影响。HTTP `401` 说明探测收到了响应，真实模型请求和流式稳定性需要另测。在 macOS 上还可参考睡眠和唤醒附近的错误。

### 界面显示「正在重新连接 1/5」

先按出现时间查 `audit`。`model_retry_rows_by_reason` 区分发送失败、响应流传输中断、模型容量和限流，WebSocket 与 SSE 的错误有单独计数。对照同一轮的后续响应和工具结果，确认执行到了哪一步。有副作用的操作如果结果不明，先查实际状态再决定是否重试。

macOS 可以运行 `codex-latency-doctor network --samples 3 --compare-direct --compare-macos-proxy`。第三条线路显式经过系统固定 HTTPS 代理，独立于 curl 继承的代理。它是可用性对照，PAC、自动发现和按目标地址选路需单独核查。直连失败、系统代理成功时，以实际客户端线路和流式日志为依据；普通 HTTPS 成功只能说明采样时线路可达。如果断流紧邻休眠或唤醒，再核对 Codex 运行时的防闲置休眠状态。

如果长任务跨越了已确认的休眠区间，可在接通电源、保持开盖时，用 `caffeinate -s -t 1800` 做一次最多 30 分钟的对照。它在到时或按 Ctrl+C 时释放临时防休眠断言。对照时看同类任务的断流、压缩耗时及是否从上一步续跑。

所有命令默认使用 `CODEX_HOME` 指定的目录，未设置时使用用户主目录下的 `.codex`。全局参数 `--codex-home` 和 `--json` 写在子命令前。下例中的路径请换成实际 Codex 目录。

```powershell
& $doctorPython -m codex_latency_doctor --codex-home 'D:\Codex Data' --json audit --hours 24
& $doctorPython -m codex_latency_doctor network --samples 3 --compare-direct
```

```sh
codex-latency-doctor --codex-home '/path/to/codex-home' --json audit --hours 24
codex-latency-doctor network --samples 3 --compare-direct
```

### 对比不同聊天的上下文

`audit` 汇总所选 Codex 目录中的运行日志。`tasks` 按聊天查看上下文观测值，可以单独查看交互聊天或后台任务。

已安装技能时，可以直接在 Codex 聊天中发送这条指令。

```text
$codex-latency-doctor 对比最近 24 小时的交互聊天和后台任务。列出各组的日志覆盖、上下文峰值、压缩阈值和自动压缩请求数。
```

```powershell
& $doctorPython -m codex_latency_doctor tasks --hours 24 --scope interactive --limit 10
& $doctorPython -m codex_latency_doctor tasks --hours 24 --scope background --limit 10
```

```sh
codex-latency-doctor tasks --hours 24 --scope interactive --limit 10
codex-latency-doctor tasks --hours 24 --scope background --limit 10
```

| 先看什么 | 怎么读 |
| --- | --- |
| 覆盖情况 | 查看实际扫描时段、是否截断、匹配的聊天数以及显示的条数。`--limit` 只控制显示条数。来源无法分类时，用 `--scope all` 将其纳入查看。 |
| 压缩阈值和完整上下文窗口 | 阈值是当前上下文计量范围下的压缩触发条件。完整窗口是模型或运行时支持的容量。同一聊天若出现多个观测值，应先确认是否换过模型或配置。 |
| 采样峰值和自动压缩请求数 | 峰值来自日志采样。只有同一条日志同时提供阈值和用量时，才可统计超阈值样本。自动压缩请求数来自识别到的记录，缺少样本时视为未知。 |

`task_…` 仅用于当前报告。对照时选同类聊天，并记录可复查的任务结果、配置和负载。`tasks` 的上下文统计无法识别具体读取了哪些记忆。需要判断是否更快时，再比较下面的耗时和长尾指标。

上下文压缩或换到新聊天后，沿用上一步已核实的产物，从待办步骤继续。输入和目标状态未变时，复用近期的证据。需要跟进运行中的任务直至完成时，先限时等待状态变化或新输出，再查询。排查时若状态相同且已无可推进的检查，就结束本次排查。

### 压缩后总是重读文件或旧图

在包含 `harness` 的源码或技能版本里，可以让 Codex 在聊天压缩或恢复时收到一条简短提示，直接执行交接中尚未完成的一步。已验收的截图沿用原结论，出现新复现、相关改动或用户要求时再打开。提交结果不明时先查回执。普通工具调用和新聊天启动不执行这个钩子。

```text
$codex-latency-doctor 预览并安装恢复提示，检查本机登记情况。
```

CLI 可依次运行 `codex-latency-doctor harness install --dry-run`、`codex-latency-doctor harness install` 和 `codex-latency-doctor harness status`。Windows 使用上文的 `& $doctorPython -m codex_latency_doctor` 加同样的参数。看到 `registered` 表示登记成功。首次使用时，按所在界面审阅这条钩子。

- **Codex 桌面版**　打开「设置」→「钩子」，在「来自配置」中选择「用户配置」。展开 `SessionStart`，找到 **Codex Latency Doctor recovery**，核对匹配器为 `compact|resume` 后，点击该条的「信任」。列表为空时可点「重新加载钩子」。
- **Codex 终端版**　在终端运行 `codex`。启动时若出现 **Hooks need review**，选 **Review hooks**。如果已进入输入框，输入 `/hooks` 并回车。在事件列表选中 `SessionStart` 并回车，再选中 **Codex Latency Doctor recovery**。详情底部显示 `t trust` 时，按 `t` 信任这一条。

卸载用 `harness remove`。实际效果看下次恢复后的第一项工具调用是否推进待办。[设计依据与验收示例](skills/codex-latency-doctor/references/recovery.md)

复盘指定对话时，从对话链接复制 ID，运行 `codex-latency-doctor --json review THREAD_ID`。首次列出最近 5 个已结束轮次的耗时、压缩、命令、看图和显式睡眠次数。下次加 `--after CURSOR`，传入上次该对话返回的 `next_cursor`。有后续页时继续取，成功返回空页表示当前索引没有新增。若返回 `current_segment`，结果只覆盖当前续跑分段。换段导致旧游标失效时会明确报错。首次的 `older_turns_omitted` 表示还有更早轮次。看图次数本身不能证明重复查看了旧图。Windows 使用上文的 `& $doctorPython -m codex_latency_doctor` 加同样参数。

排查两天内的频繁压缩时，把 `audit` 和 `tasks` 的窗口设为 `--hours 48`。分别看开轮前和执行中触发的请求、压缩接口的 HTTP 状态和配对耗时。同一次压缩可能记录多条 HTTP 响应，配对耗时也可能包含休眠。结合压缩前最后一个已完成步骤和压缩后的实际动作判断是否重做或漏掉验收。不同模型或任务的阈值可能不同，调参前先选同模型、同计量范围的长任务做对照。

### 对比优化前后

1. **保存基线。** 优先使用已有的可比审计报告，或在改配置前执行一次有代表性的只读或隔离任务，再保存 JSON 报告。记录模型、推理档、输入、已有上下文和工具负载。涉及发布、发消息或修改真实数据的任务，使用预演或测试数据复测。
2. **一次只改一项。** 如需更改配置，按下面的修改流程保留备份并确认设置生效。优化后执行一次相同的安全任务。需要更多样本时，按明确的测量实验安排。让两次报告的观察窗口等长且互不重叠。示例使用 1 小时窗口，两次采集间隔应超过 1 小时，并在各自窗口内完成相应测试。
3. **查看对比。** 查看基线、当前值和变化量，再核对两次报告的扫描覆盖与有效样本数。重点看响应头延迟及长尾、压缩请求、模型重试和工具耗时。用户可见的首条回复和任务总耗时可以另外计时。样本不足时，报告已有发现和缺失的证据，结束本次对比。

**Windows PowerShell**

```powershell
# 改动前执行，并保留 before.json。
& $doctorPython -m codex_latency_doctor --json audit --hours 1 | Set-Content -LiteralPath .\before.json -Encoding utf8

# 等优化后的对照任务完成，再执行下面两行。
& $doctorPython -m codex_latency_doctor --json audit --hours 1 | Set-Content -LiteralPath .\after.json -Encoding utf8
& $doctorPython -m codex_latency_doctor compare .\before.json .\after.json
```

**macOS 和 Linux**

```sh
# 改动前执行，并保留 before.json。
codex-latency-doctor --json audit --hours 1 > before.json

# 等优化后的对照任务完成，再执行下面两行。
codex-latency-doctor --json audit --hours 1 > after.json
codex-latency-doctor compare before.json after.json
```

`compare` 读取本地的两份 `--json audit` 报告。自动处理时用 `--json compare before.json after.json`。每份 `audit` 都会汇总窗口内所有聊天，因此测试期间尽量避免混入无关任务。

如果两份报告已保存，也可以在 Codex 聊天中发送下面的指令。文件位于其他目录时，请写出完整路径。

```text
$codex-latency-doctor 比较 before.json 和 after.json。先核对采集窗口、日志覆盖和样本数，再解释有哪些指标变化。
```

先看两份报告的时间窗口是否等长、扫描是否截断、采集时间是否相隔足够久。差值是当前值减去基线值。工具只在观测窗口和有效数据允许时计算差值。旧版报告如果缺少采集时间，需要自行核对窗口有无重叠。

| 对比指标 | 应结合哪些证据 |
| --- | --- |
| 模型请求响应头等待 | 对照中位数、最大值和至少 30 秒的等待次数，并核对两边成功配对的样本数。 |
| 上下文压缩 | 对照配置及运行时阈值、自动压缩请求数和压缩耗时。确认两组任务的模型及上下文负载相近。 |
| 模型错误及工具耗时 | 对照重试、HTTP 错误和流式错误的记录数。工具耗时还要结合完成调用的样本数及工具类型。 |

下方是使用合成日志得到的输出片段。两列都只有一个配对样本，适合演示怎么看差值。

```text
Metric                                         Before        After       Change  Samples (before/after)
Step to HTTP headers median (s)                  45.0         15.0        -30.0  1 / 1
Paired header waits >= 30s                          1            0           -1  1 / 1
```

### 修改、验证和回滚

根据证据选一项修改，先按上一节保存基线。对照时使用同一个 Codex 目录、同一套 CLI 或桌面应用和同一个实际使用的模型。聊天内选择的推理档或 Codex CLI 的 `-c` 覆盖项可能优先于 `config.toml` 的默认值。

| 观察到的证据 | 可以执行的修改 | 修改后怎么确认 |
| --- | --- | --- |
| 同类长任务频繁自动压缩，每次压缩有明显等待 | 先收窄不必要的工具输出。仍频繁触发时，按实际模型窗口和上下文用量选一个候选阈值，用 `--compact-tokens N` 试验。下文的 120,000 是命令示例。 | 用同模型的新任务确认实际阈值，再比较执行中压缩次数、耗时和任务完成质量。 |
| 同类请求反复出现长等待，任务允许降低推理档 | `--reasoning-effort medium`。工具接受 `low`、`medium`、`high`，选择模型支持的档位。 | 用上一节的安全任务对照耗时和答复质量。案例中进一步调低的收益并不稳定。 |
| 启动日志反复指向同一个 MCP 服务，且当前工作流允许暂停它 | `--disable-mcp NAME`。把 `NAME` 换成已有 `[mcp_servers.NAME]` 配置表的服务名，支持英文字母、数字、连字符和下划线。修复服务后可用 `--enable-mcp NAME` 恢复。 | 检查对应启动报错，并在隔离环境中测试依赖该服务的工作流。报错消失只表明对应的启动问题解除。 |
| macOS 空闲睡眠与任务中断重合 | `--prevent-idle-sleep`。合盖仍可触发系统睡眠。 | 重新打开桌面应用，在任务运行期间用 `audit` 查看 `power.codex_idle_sleep_assertion`，任务结束后再核对睡眠事件。 |

如果 HTTPS 探测证据不足，先复核真实模型请求和流式稳定性，再决定是否需要调整代理。上表的命令只修改指定的 Codex 设置。

关闭其他会写入配置的程序。先用 `apply --dry-run` 预览，存在改动时输出 `status: planned` 和变更项，整个预览过程保持文件原样且不创建备份。确认后去掉 `--dry-run` 执行，工具会把原配置备份到 Codex 目录下的 `latency-doctor-backups/`，再原子替换 `config.toml`。看到 `status: applied` 后，记下返回的 `backup_id`。如果原配置已经符合要求，工具会返回 `status: unchanged`。

**Windows PowerShell**

```powershell
& $doctorPython -m codex_latency_doctor apply --dry-run --compact-tokens 120000
& $doctorPython -m codex_latency_doctor apply --compact-tokens 120000
& $doctorPython -m codex_latency_doctor verify
```

**macOS 和 Linux**

```sh
codex-latency-doctor apply --dry-run --compact-tokens 120000
codex-latency-doctor apply --compact-tokens 120000
codex-latency-doctor verify
```

已安装技能时，也可以在 Codex 聊天中直接指明要修改的设置。

```text
$codex-latency-doctor 在当前 Codex 目录先预览，再将自动压缩阈值改为 120000。只改这一项，执行校验并告诉我备份 ID。
```

应用改动后运行一次 `verify`。它用 `PATH` 上的 Codex CLI 启动新进程执行严格配置检查。需要比较桌面应用时，等活动任务完成，必要时重新打开应用，从界面或新任务的运行日志确认改动生效，再做对比。启动校验失败或无法确认运行时已生效时，记录当前结果，停止本次对比。

校验失败或出现退化后，如果决定回滚，用已确认的备份 ID 替换下面的 `BACKUP_ID`。该 ID 通常可以从 `apply` 的结果中获取。如果配置在应用改动后又被编辑过，回滚会拒绝覆盖当前文件。此时可在本地对照备份，手工合并要撤销的设置。

```powershell
& $doctorPython -m codex_latency_doctor rollback BACKUP_ID
& $doctorPython -m codex_latency_doctor verify
```

```sh
codex-latency-doctor rollback BACKUP_ID
codex-latency-doctor verify
```

恢复后，使用桌面应用时等活动任务结束再重新打开，并用只读或隔离任务复查受影响的工作流。

#### 写入中断后查找备份

如果 `apply` 在返回结果前中断，使用原参数和同一个 Codex 目录运行 `apply --dry-run`。返回 `planned` 时，先根据原请求核对尚待写入的变更。返回 `unchanged` 时，说明文件已经包含目标设置，可以运行 `backups` 查找备份 ID。

```powershell
& $doctorPython -m codex_latency_doctor --json backups
```

```sh
codex-latency-doctor --json backups
```

已安装技能时，也可以在 Codex 聊天中请求查询。

```text
$codex-latency-doctor 上一次 apply --compact-tokens 120000 的结果丢失了。请在同一个 Codex 目录做预览，如果目标设置已经在磁盘上，就列出匹配的备份 ID。
```

如果 `apply` 指定了 `--codex-home`，在 `backups` 前传入同一个目录。只读命令在 `matching_backup_ids` 中列出保存的摘要与当前配置匹配的备份。匹配只说明文件状态一致。还需根据独立记录确认备份来自这次中断的操作，例如可查到的原命令输出。无匹配、有多个匹配或无法确认来源时，暂缓回滚。确定要恢复时，再对已确认的 ID 执行 `rollback`。

## 安装和使用遇到问题

| 现象 | 排查方式 |
| --- | --- |
| `py` 或 `python3` 找不到，或者版本过低 | 先检查上面的解释器命令。安装符合要求的版本后重新打开终端。已经安装多个版本时，可显式选择，例如 Windows 的 `py -3.12` 或 macOS、Linux 的 `python3.12`。 |
| 日志为 `missing`、`unavailable` 或 `unsupported_schema` | 确认与出现问题的 Codex 实例使用的是同一个目录。用 `--codex-home` 指向实际目录。运行一次新任务后重查。不同版本的日志结构可能有差异，读取不到的指标按未知处理。 |
| `verify` 找不到或无法启动 Codex CLI | Windows 运行 `Get-Command codex -All` 和 `codex --version`。macOS 或 Linux 运行 `command -v codex` 和 `codex --version`。如果只有桌面端，安装 Codex CLI 并确保它在 `PATH` 上。Windows 支持原生 `codex.exe` 和正常安装的官方 npm 版本，后者需要 Node.js。严格启动校验还需要当前 CLI 支持相应的 `app-server` 参数。 |
| `network` 找不到 curl | Windows 用 `Get-Command curl.exe` 检查真正的可执行文件。macOS 或 Linux 用 `command -v curl`。安装 curl 或修正 `PATH` 后重试。 |
| `tasks` 交互聊天为空 | 试 `--scope all`。来源未知的聊天不会进入交互组。若仍为空，所选窗口可能没有可识别的上下文采样。 |
| `power.status` 显示 `unsupported_platform` | Windows 和 Linux 均会显示此状态。日志、配置及网络检查仍可使用。 |

## 本地数据和分享

`audit` 和 `tasks` 只读扫描有限范围的本地日志。`tasks` 使用本地状态库的聊天 ID 与来源来分组。报告省略原始日志、聊天 ID 和标题、凭据、配置 URL 及项目路径。分享前检查时间与 token 计数等工作负载细节。配置备份可能含有密钥，请保存在本地。
