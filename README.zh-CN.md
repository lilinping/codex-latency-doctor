# Codex Latency Doctor 中文指南

Codex 长时间没有可见进度时，用这个本地工具查看上下文压缩、模型请求重试、工具耗时和 HTTPS 连通性。你可以预览并应用有依据的配置修改，然后验证、对比或回滚。macOS 还可以检查睡眠和唤醒事件。项目采用 MIT 许可证。

[English README](README.md) · [真实排查案例](CASE-STUDY.zh-CN.md) · [发布版本](https://github.com/lilinping/codex-latency-doctor/releases)

## 安装教程

支持 Windows 10/11、macOS 和 Linux。需要 Python 3.11 或以上版本。先核对版本。

- **Windows PowerShell** 运行 `py -3 --version`。如果没有 `py` 启动器，运行 `python --version`，确认版本后将安装命令中的 `py -3` 换成 `python`。
- **macOS 和 Linux** 运行 `python3 --version`。如果系统解释器较旧，使用已安装的新版本，例如 `python3.12`。

如果还没有符合要求的解释器，安装后重新打开终端。[Python 官方下载](https://www.python.org/downloads/)

### 在 Codex 中安装技能

在 Codex 聊天中发送：

```text
$skill-installer 请从 https://github.com/lilinping/codex-latency-doctor/tree/v0.5.0/skills/codex-latency-doctor 安装技能
```

安装后新建聊天并运行：

```text
$codex-latency-doctor 只读排查最近 24 小时的卡顿，给出日志覆盖情况、可能卡在哪一步，以及下一项验证。
```

技能已包含诊断程序。安装后使用现有的 Python 运行本地检查，无需 pip。技能列表还未显示此技能时，等活动任务结束后重启 Codex。

如果已安装 v0.4.0，从 Codex 目录下的 `skills` 中把 `codex-latency-doctor` 目录移到扫描范围之外做备份，例如放到 Codex 目录下的 `codex-latency-doctor-v0.4.0-backup`。然后用上面的指令安装 v0.5.0 并新建聊天。默认 Codex 目录是用户主目录下的 `.codex`，自定义位置以 `CODEX_HOME` 为准。

### Windows PowerShell 安装 CLI

在自己有写权限的目录中执行以下命令。后续 PowerShell 示例也从这个目录运行。

如果 Codex 运行在 WSL 中，请在该 WSL 发行版里按 Linux 步骤安装和诊断。Windows 原生 Codex 在 PowerShell 中诊断。

```powershell
py -3 -m venv .venv
$doctorPython = '.\.venv\Scripts\python.exe'
& $doctorPython -m pip install 'https://github.com/lilinping/codex-latency-doctor/releases/download/v0.5.0/codex_latency_doctor-0.5.0-py3-none-any.whl'
& $doctorPython -m codex_latency_doctor --version
& $doctorPython -m codex_latency_doctor audit --hours 24
```

直接调用虚拟环境中的 `python.exe` 无需激活虚拟环境，也无需修改 PowerShell 执行策略。新开 PowerShell 窗口时，先回到安装目录并重新设置上面的 `$doctorPython`。

### macOS 和 Linux 安装 CLI

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'https://github.com/lilinping/codex-latency-doctor/releases/download/v0.5.0/codex_latency_doctor-0.5.0-py3-none-any.whl'
codex-latency-doctor --version
codex-latency-doctor audit --hours 24
```

## 使用教程

### 找出一次卡顿可能发生在哪一步

1. 记录卡顿的大致时间。立即执行 `audit --hours 1`，或者用 `--hours` 选择覆盖问题时段的范围，取值 1 至 168 小时。先看 `logs.status`、最旧的扫描记录和 `truncated`。如果实际数据覆盖不到该时段，后续只能给出有限结论。
2. 对照配置值、运行时压缩阈值、自动压缩次数及耗时，再看模型重试和错误。模型步骤到 HTTP 响应头的计时包含本地准备和可能的重试，截止于首次成功收到响应头，后续流式输出需另查。已结束的工具调用有单独的计时。
3. 怀疑网络时运行 `network --samples 3 --compare-direct`。默认线路使用 curl 继承的代理环境，对照线路绕过 curl 代理。两者仍可能受到系统 VPN 影响。HTTP `401` 说明探测收到了响应，真实模型请求和流式稳定性需要另测。在 macOS 上还可参考睡眠和唤醒附近的错误。

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

### 对比优化前后

1. **保存基线。** 在改配置之前先跑一组代表性的任务。记录模型、推理档、输入、已有上下文、工具负载和任务次数。任务结束后马上保存 JSON 报告。
2. **一次只改一项。** 如需更改配置，先按下面的修改流程保留备份。在新进程中核对配置生效后，重复同样的任务。让两次报告的观察窗口等长且互不重叠。示例使用 1 小时窗口，请让两次采集间隔超过 1 小时，并在各自窗口内完成相应测试。
3. **查看对比。** 查看基线、当前值和变化量，再核对两次报告的扫描覆盖与有效样本数。重点看响应头延迟及长尾、压缩请求、模型重试和工具耗时。样本过少、覆盖不全或负载不同，变化只能作为线索。用户可见的首条回复和任务总耗时可以在重复任务时另外计时。

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
| 同类长任务频繁自动压缩，每次压缩有明显等待 | `--compact-tokens 120000`。结合实际模型窗口和上下文用量选择阈值。案例确认了 120,000 生效，长任务能节省多少时间仍缺少受控对照。 | 用新任务的日志确认阈值，再比较压缩次数、耗时和任务完成质量。 |
| 同类请求反复出现长等待，任务允许降低推理档 | `--reasoning-effort medium`。工具接受 `low`、`medium`、`high`，选择模型支持的档位。 | 重复同样的任务，同时检查耗时和答复质量。案例中进一步调低的收益并不稳定。 |
| 启动日志反复指向同一个 MCP 服务，且当前工作流允许暂停它 | `--disable-mcp NAME`。把 `NAME` 换成已有 `[mcp_servers.NAME]` 配置表的服务名，支持英文字母、数字、连字符和下划线。修复服务后可用 `--enable-mcp NAME` 恢复。 | 检查对应启动报错，并测试依赖该服务的工作流。报错消失只表明对应的启动问题解除。 |
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

`verify` 用 `PATH` 上的 Codex CLI 启动新进程执行严格配置检查。使用桌面应用时，等活动任务完成后重新打开应用，重跑相同任务并检查改动是否生效。再用 `compare` 查看两次报告，核对预期指标和任务质量。

校验失败或出现退化时，用 `apply` 返回的实际 ID 替换下面的 `BACKUP_ID`。如果配置在应用改动后又被编辑过，回滚会拒绝覆盖当前文件。此时可在本地对照备份，手工合并要撤销的设置。

```powershell
& $doctorPython -m codex_latency_doctor rollback BACKUP_ID
& $doctorPython -m codex_latency_doctor verify
```

```sh
codex-latency-doctor rollback BACKUP_ID
codex-latency-doctor verify
```

恢复后，使用桌面应用时等活动任务结束再重新打开，并复查受影响的工作流。

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
