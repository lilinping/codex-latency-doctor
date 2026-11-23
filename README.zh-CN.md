# Codex Latency Doctor 中文指南

这是一个运行在本地的 Codex 卡顿诊断工具。聊天长时间没有可见进度时，可以用它查看上下文压缩、模型请求重试、工具耗时以及 macOS 睡眠事件。项目采用 MIT 许可证。

[English README](README.md) · [真实排查案例](CASE-STUDY.zh-CN.md) · [发布版本](https://github.com/lilinping/codex-latency-doctor/releases)

## 在 Codex 中安装技能

在 macOS 或 Linux 上使用，需要 Python 3.11 或以上版本。技能自带诊断程序，安装后可离线运行只读检查。在 Codex 中发送这条消息安装 `v0.3.0` 的技能。

```text
$skill-installer 请从 https://github.com/lilinping/codex-latency-doctor/tree/v0.3.0/skills/codex-latency-doctor 安装技能
```

安装完成后，新建一个 Codex 聊天并发起一次只读排查。如果技能列表还没出现此技能，等活动任务结束后重启 Codex。

```text
$codex-latency-doctor 只读检查最近 24 小时的卡顿，并比较最近有上下文采样的交互聊天。
```

## 在终端安装命令行工具

如果需要独立运行和导出 JSON，可在虚拟环境中安装发布版。如果 `python3 --version` 低于 3.11，把下方的 `python3` 换成已安装的新版解释器，例如 `python3.12`。`network` 需要 `curl`，`verify` 需要 `PATH` 上有 `codex` 命令。

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'https://github.com/lilinping/codex-latency-doctor/releases/download/v0.3.0/codex_latency_doctor-0.3.0-py3-none-any.whl'
codex-latency-doctor audit --hours 24
codex-latency-doctor tasks --hours 24 --scope interactive --limit 5
```

命令默认读取 `CODEX_HOME` 指定的目录，未设置时使用 `~/.codex`。`--codex-home` 和 `--json` 是全局选项，放在子命令前。

```sh
codex-latency-doctor --codex-home /path/to/codex-home --json tasks --hours 24 --scope all
```

## 怎么判断卡在哪里

| 命令 | 怎么读结果 |
| --- | --- |
| `audit --hours 24` | 先确认日志覆盖了问题时段。对照配置中的压缩阈值与运行时观测值，并查看模型重试、请求响应头、工具耗时和压缩记录。macOS 上还会检查睡眠及唤醒附近的模型错误。请求步骤到响应头的计时可以包含本地准备和重试，范围止于成功响应的 HTTP 头。 |
| `tasks --hours 24 --scope interactive --limit 5` | 查看交互聊天的实际压缩阈值、完整上下文窗口、采样峰值和自动压缩请求数。用 `--scope background` 查后台聊天，用 `--scope all` 纳入来源未知的聊天。`--limit` 只控制展示条数。 |
| `network --samples 3 --compare-direct` | 用无认证 HTTPS 探测对比终端当前线路和绕过 curl 代理的线路。终端当前线路使用 curl 继承的代理环境。系统 VPN 仍可能影响两条线路。HTTP `401` 表示收到 HTTP 响应。真实模型请求的鉴权、流式稳定性和耗时需要另测。 |
| `verify` | 用当前 PATH 上的 Codex CLI 严格校验启动配置。新进程读到配置后，可通过新桌面任务的运行日志核对实际生效值。 |

上下文采样缺失时按未知处理。`task_…` 标签只在单份报告中有效。跨任务做参考时，记录能复查的事实、产物和适用条件。优先用相同模型、推理档、输入、上下文和工具负载比较首条可见进度、总耗时和长尾等待。上下文用量和压缩记录无法识别具体读取了哪些记忆，也无法单独证明提速。

## 修改和回滚

先用只读命令确认症状。macOS 空闲睡眠确实中断工作时，可以开启运行期间防空闲睡眠。若频繁压缩影响工作，再按实际模型窗口和长任务对照选择阈值。下方 `120000` 是案例值，请先确认适合自己的模型及工作负载。合盖睡眠仍由 macOS 处理。

关闭 Codex 设置编辑器及其他配置写入程序，再选择需要的参数。`apply` 读取现有 `config.toml`，只写入所选配置，并将原文件备份到当前 Codex 目录下的 `latency-doctor-backups/`。

```sh
# 确认需要调整阈值后，才加上 --compact-tokens 120000。
codex-latency-doctor apply --prevent-idle-sleep
codex-latency-doctor verify
```

记下输出中的 `backup_id`。等活动任务结束后重新打开桌面应用，运行一次可比的长任务，再用 `audit` 和 `tasks` 检查阈值、防睡眠状态和任务表现。回滚时将下面的 `BACKUP_ID` 替换成实际值。

```sh
codex-latency-doctor rollback BACKUP_ID
codex-latency-doctor verify
```

如果配置在应用改动后又被编辑过，回滚会拒绝覆盖，需要手动合并以保留之后的修改。

## 本地数据与分享

- `audit` 和 `tasks` 只读扫描有限窗口内的本地运行日志。`tasks` 还使用本地状态数据库中的聊天 ID 和来源元数据来分组。日志格式随 Codex 版本变化，扫描截断时按报告显示的覆盖范围解释。
- 报告省略原始日志、聊天 ID 和标题、凭据、配置 URL 和项目路径。分享前检查时间和 token 计数等工作负载细节。配置备份可能包含密钥，请保存在本地。
- 工具只写入上述两项配置。发现不存在的受信任项目路径时，先确认磁盘或外部卷是否挂载。
