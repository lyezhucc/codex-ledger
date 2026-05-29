# codex-ledger

本地 Codex CLI token 使用量统计工具。从 `~/.codex/sessions/` 读取 rollout JSONL 日志，用增量算法计算每日 token 消耗。

## 快速开始

```bash
# 默认扫描 ~/.codex，统计 2026-05-03 至今
python3 codex_usage_local.py

# 指定起始日期和时区
python3 codex_usage_local.py --since 2026-05-03 --tz Asia/Shanghai ~/.codex

# 多账号
python3 codex_usage_local.py --since 2026-05-03 ~/.codex ~/account-b/.codex

# 自定义输出目录 + JSON + debug
python3 codex_usage_local.py --since 2026-05-03 --out ~/Desktop/report --json --debug
```

## 输出

终端打印汇总后，导出以下 CSV 到 `--out` 目录（默认 `~/Desktop/codex-ledger-report`）：

| 文件 | 内容 |
|------|------|
| `raw_events.csv` | 每次增量事件的明细 |
| `daily_by_model.csv` | 按天+模型汇总 |
| `daily_by_account_model.csv` | 按天+账号+模型汇总 |
| `model_total.csv` | 按模型汇总 |
| `account_total.csv` | 按账号/目录汇总 |
| `grand_total.csv` | 总量（一行） |
| `report.json`（需 `--json`） | JSON 格式完整报告 |

## 统计口径

### 为什么不能直接相加 total_token_usage

Codex 在每个 session/rollout 中会多次发出 `token_count` 事件。其中的 `total_token_usage` 是**该 rollout 从开始到此刻的累计值**，而不是单次消耗。

**示例：** 同一个 rollout 内有 3 条 token_count：

| 事件 | total_token_usage | 正确增量 |
|------|-------------------|---------|
| 第 1 条 | 1000 | 1000 |
| 第 2 条 | 1500 | 500 |
| 第 3 条 | 1500 | 0（跳过） |

- ✅ 正确总量：1000 + 500 = **1500**
- ❌ 直接相加：1000 + 1500 + 1500 = **4000**

### cached_input_tokens 不计入总量

`cached_input_tokens` 是 `input_tokens` 的子集（被缓存命中的那部分）。`total_tokens` 已经正确包含了 input + output + reasoning 的总和。**不要**把 `cached_input_tokens` 额外加到 `total_tokens` 里。

### delta 为负数的处理（fork 回溯修正）

如果相邻两次 `total_token_usage` 出现下降（`当前 < 上一个`），说明发生了 fork/resume 导致计数器重置。工具会：

1. **回溯移除上一个错误增量**（来自 fork 继承的历史值，已在原始 session 中统计过）
2. 用当前值作为新一段的增量
3. 记录 warning

### 首个 token_count 启发式检测

如果某个 rollout 文件的第一个 `token_count` 的 `total_tokens` 超过 500 万，工具会发出 warning——因为正常新 session 的首次消耗远小于此值，大概率是 fork 继承的历史累计。这个阈值可根据实际使用调整（修改 `FORK_SUSPICIOUS_THRESHOLD`）。

### 午夜边界安全余量

文件路径的日期粗过滤会多保留 1 天的余量（since_date 前一天的目录也会扫描），防止 UTC→本地时区转换导致午夜边界事件丢失。最终仍以事件时间戳归日为准。

### 日期归日

按 `--tz` 指定的时区（默认 `Asia/Shanghai`）转换 UTC 时间戳后归到对应日期。文件名中的日期只做粗过滤，最终以事件 `timestamp` 字段为准。

## 多账号目录

Codex 支持多 profile。如果每个 profile 有独立的 `.codex` 目录：

```bash
python3 codex_usage_local.py \
  ~/.codex \
  ~/codex-profiles/work/.codex \
  ~/codex-profiles/personal/.codex
```

工具会按目录路径推断账号名称（取 `.codex` 父目录名）。如果多个 profile 共用同一个 `~/.codex`，工具只能统计目录总量，无法区分账号。

## 局限性

1. **Codex Web / App / 远程任务** 如果没有写入本地 `~/.codex/sessions`，本工具统计不到。
2. **`codex exec --json`** 这类 headless 输出的 token 不走普通 session 目录，需要额外处理。
3. **fork / resume session**：工具会通过负 delta 回溯修正和首个 token 启发式检测来减少 fork 导致的重复统计。但如果 fork 后计数器从相同值连续增长（无负 delta），仍可能漏检。建议人工核对 daily_by_model.csv 中的异常 spike。
4. **无时间戳的 token_count**：优先从文件名解析；失败则跳过并记录 warning。

## 依赖

- Python 3.10+
- 纯标准库，无第三方依赖

## 测试

```bash
python3 tests/test_sample.py
```

测试用构造的 `tests/fixtures/rollout-sample.jsonl` 验证增量统计算法是否正确。

## 项目结构

```
codex-ledger/
├── codex_usage_local.py    # 主工具
├── README.md
├── tests/
│   ├── fixtures/
│   │   └── rollout-sample.jsonl
│   └── test_sample.py
└── output/
```
