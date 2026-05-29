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

# 自定义输出 + JSON + debug
python3 codex_usage_local.py --since 2026-05-03 --out ~/Desktop/report --json --debug

# 启用路径日期粗过滤（加速但可能漏长会话）
python3 codex_usage_local.py --since 2026-05-03 --fast-path-filter ~/.codex
```

## 给普通用户的使用教程

### 1. 下载项目

```bash
git clone https://github.com/lyezhucc/codex-ledger.git
cd codex-ledger
```

### 2. 运行测试（确认环境正常）

```bash
bash run_tests.sh
```

### 3. 统计默认 Codex 账号

```bash
python3 codex_usage_local.py \
  --since 2026-05-03 \
  --tz Asia/Shanghai \
  --out ~/Desktop/codex-ledger-report \
  --json \
  ~/.codex
```

### 4. 查看结果

```bash
open ~/Desktop/codex-ledger-report
```

重点看：

| 文件 | 用途 |
|------|------|
| `grand_total.csv` | 总 token |
| `model_total.csv` | 按模型统计 |
| `daily_by_model.csv` | 按天+模型统计 |
| `raw_events.csv` | 原始增量事件，排查异常用 |
| `report.json` | 完整 JSON 报告 |

### 5. 统计多个 Codex 目录

```bash
python3 codex_usage_local.py \
  --since 2026-05-03 \
  --tz Asia/Shanghai \
  --out ~/Desktop/codex-ledger-report \
  --json \
  ~/.codex \
  ~/codex-profiles/work/.codex \
  ~/codex-profiles/personal/.codex
```

### 6. 常见问题

**为什么统计很慢？**
默认是全量扫描，准确优先。可以加 `--fast-path-filter` 加速，但它可能漏掉跨很多天的长会话。

**为什么统计不到 Codex Web 的用量？**
本工具只读取本机 `~/.codex/sessions`。如果 Codex Web/App/远程任务没有写到本机日志，就统计不到。

**为什么不能直接看 total_token_usage 相加？**
因为它是一个 rollout 内的累计值，不是单次消耗。直接相加会严重高估。

## Codex 对话式使用

本仓库包含 Skill 定义，让 Codex 能在对话中自动调用本工具。

Codex 读取 `AGENTS.md` 和 `skills/codex-ledger/SKILL.md` 后，用户只需说：

> "统计我从 5 月 3 日开始 Codex 用了多少 token，按模型列出来"

Codex 会自动执行统计并给出中文总结。

Skill 文件：
- `skills/codex-ledger/SKILL.md` — 告诉 Codex 何时触发、如何运行、如何解释结果
- `AGENTS.md` — 仓库级 agent 指令
- `skills/codex-ledger/templates/report_summary_template.md` — 输出格式模板
- `skills/codex-ledger/examples/sample_commands.md` — 常用命令示例

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

`cached_input_tokens` 是 `input_tokens` 的子集（被缓存命中的那部分）。`total_tokens` 已经正确包含了 input + output 的总和。**不要**把 `cached_input_tokens` 额外加到 `total_tokens` 里。

### fork/resume 处理（启发式，不保证 100% 准确）

工具通过两层机制减少 fork 导致的重复统计：

1. **首个 token_count 启发式检测**：如果某个 rollout 文件的第一个 `token_count` 的 `total_tokens` 超过 500 万（`FORK_SUSPICIOUS_THRESHOLD`），标记为 *suspicious inherited baseline*。正常新 session 的首次消耗远小于此值。

2. **负 delta 条件回溯**：当相邻两次 `total_token_usage` 下降（`当前 < 上一个`）时：
   - 如果上一条被标记为 *suspicious* → **回溯移除**上一条错误增量（来自 fork 继承的历史值，已在原始 session 中统计过），用当前值作为新段增量。
   - 如果上一条不是 suspicious → **保留上一条**，只记录 warning，把当前值作为新一段的增量。

**注意**：如果 fork 后计数器从相同值连续增长（无负 delta），仍可能漏检。建议人工核对 `daily_by_model.csv` 中的异常 spike，并与 OpenAI / Codex Usage Dashboard 做总量交叉校验。

### `--since` 边界处理

`--since` 过滤发生在增量计算**之前**：即使事件在范围外，也会推进 `prev_tokens` 基线。这确保了跨 `--since` 边界的 rollout 文件不会把完整累计值误当增量。

### 日期归日

按 `--tz` 指定的时区（默认 `Asia/Shanghai`）转换 UTC 时间戳后归到对应日期。

### 默认全量扫描

**默认扫描所有 `rollout-*.jsonl` 文件**，按事件 `timestamp` 精确过滤。这是最准确的方式。

使用 `--fast-path-filter` 可以启用路径日期粗过滤（>=14 天 buffer），减少扫描文件数，但**可能漏掉跨越多日的长会话**。

### 缺失字段容错

- `reasoning_output_tokens`、`cached_input_tokens` 缺失 → 默认 0
- `total_tokens` 缺失 → 尝试 `input_tokens + output_tokens` 推导，记录 warning
- 仅当 `total_token_usage` 整体缺失时才跳过该事件

### token_count 识别

不强依赖外层 `type == "event_msg"`。只要 `payload` 是 dict 且 `payload.type == "token_count"`，就处理。

### model 识别

每行 JSON 都递归查找 `model` 字段，找到即更新当前 model，不限于特定事件类型。

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
3. **fork / resume session**：fork 处理是启发式的，不保证 100% 准确。建议与 OpenAI / Codex Usage Dashboard 做总量交叉校验。
4. **`--fast-path-filter`** 可能漏掉跨越多日的长会话（至少 14 天 buffer，但无法覆盖极端情况）。
5. **无时间戳的 token_count**：优先从文件名解析；失败则跳过并记录 warning。

## 依赖

- Python 3.10+
- 纯标准库，无第三方依赖

## 测试

```bash
# 运行全部测试
python3 tests/test_sample.py
python3 tests/test_payload_type_only.py
python3 tests/test_missing_fields.py
python3 tests/test_negative_delta_no_pop.py
python3 tests/test_suspicious_fork_pop.py
python3 tests/test_since_boundary.py
```

## 项目结构

```
codex-ledger/
├── codex_usage_local.py          # 主工具
├── README.md                     # 人看
├── AGENTS.md                     # Codex / coding agent 看
├── LICENSE                       # MIT
├── run_tests.sh                  # 一键测试
├── .gitignore
├── skills/
│   └── codex-ledger/
│       ├── SKILL.md              # Skill 定义（触发条件、运行方式、输出格式）
│       ├── scripts/              # 主脚本符号链接
│       ├── templates/            # 输出格式模板
│       └── examples/             # 常用命令示例
├── tests/
│   ├── fixtures/
│   │   ├── sample/                         # 基础增量算法
│   │   ├── payload_type_only/              # 不依赖外层 event type
│   │   ├── missing_fields/                 # 缺失字段容错
│   │   ├── negative_delta_no_pop/          # 普通负 delta 不 pop
│   │   ├── suspicious_fork_pop/            # suspicious 时负 delta 回溯
│   │   ├── suspicious_then_normal/         # suspicious→正常→负delta 不误pop
│   │   └── since_boundary/                # --since 跨边界基线
│   ├── test_sample.py
│   ├── test_payload_type_only.py
│   ├── test_missing_fields.py
│   ├── test_negative_delta_no_pop.py
│   ├── test_suspicious_fork_pop.py
│   ├── test_suspicious_then_normal.py
│   └── test_since_boundary.py
└── output/
```
