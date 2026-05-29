# Codex Ledger Skill

## Purpose

Use this skill when the user asks to analyze, summarize, audit, or export OpenAI Codex CLI token usage from local `~/.codex/sessions` rollout logs.

Typical user requests:

- "统计我 Codex 从 5 月 3 日开始用了多少 token"
- "按模型看 Codex token 消耗"
- "看一下每天 Codex 用量"
- "统计所有 Codex 账号"
- "帮我分析 Codex token spike"
- "导出 Codex usage CSV"

## What this skill does

This skill runs `codex_usage_local.py` to parse local Codex CLI rollout JSONL files and produce:

- `raw_events.csv` — 每次增量事件的明细
- `daily_by_model.csv` — 按天+模型汇总
- `daily_by_account_model.csv` — 按天+账号+模型汇总
- `model_total.csv` — 按模型汇总
- `account_total.csv` — 按账号/目录汇总
- `grand_total.csv` — 总量（一行）
- optional `report.json`

The script uses delta-based accounting because `total_token_usage` in Codex rollout logs is cumulative within a rollout file.

**Do not directly sum all `total_token_usage` events.**

## Default command

If the user does not specify a start date, ask once. If the user says "从 5 月 3 日开始" and the year is clear from context, use that year.

For Chang's current workflow, default to:

```bash
python3 codex_usage_local.py \
  --since 2026-05-03 \
  --tz Asia/Shanghai \
  --out ~/Desktop/codex-ledger-report \
  --json \
  ~/.codex
```

## Multi-account usage

If the user says "所有 Codex 账号", first check whether there are multiple Codex directories.

```bash
# Check for multiple .codex directories
find ~ -maxdepth 4 -name ".codex" -type d 2>/dev/null
```

Common patterns:

```bash
~/.codex
~/codex-profiles/work/.codex
~/codex-profiles/personal/.codex
~/codex-accounts/*/.codex
```

If multiple directories exist, run:

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

If multiple accounts share the same `~/.codex`, explain that the tool can only report the directory total, not reliably separate accounts.

## Output interpretation

Read these files first:

1. `grand_total.csv` for total usage
2. `model_total.csv` for usage by model
3. `daily_by_model.csv` for usage by day and model
4. `raw_events.csv` for debugging abnormal spikes

When summarizing, always include:

- Total `total_tokens`
- `input_tokens`
- `cached_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`
- Top models by `total_tokens`
- Highest usage days
- Any warnings or suspicious spikes

## Important accounting rules

- `total_token_usage` is cumulative inside one rollout file.
- Correct method: current total minus previous total (delta).
- `cached_input_tokens` is a subset of `input_tokens`; do not add it again.
- `reasoning_output_tokens` is a detail field; do not double count it.
- Use `total_tokens` as the main total.
- `input_tokens - cached_input_tokens + output_tokens` is only a reference value, not official billing.

## Known limitations

Always mention these when the user wants a precise total:

1. Codex Web/App/remote tasks may not write to local `~/.codex/sessions`.
2. `codex exec --json` or headless usage may require additional handling.
3. Fork/resume correction is heuristic, not guaranteed 100% accurate.
4. Final totals should be cross-checked with OpenAI/Codex Usage Dashboard when available.
5. If the user enabled `--fast-path-filter`, long sessions may be missed.

## Recommended final response format

After running the script, read the output files and summarize in Chinese:

```text
## 统计完成

**时间范围：** YYYY-MM-DD 至今（Asia/Shanghai）

**总量：**
| 指标 | 数值 |
|------|------|
| total_tokens | xxx |
| input_tokens | xxx |
| cached_input_tokens | xxx |
| output_tokens | xxx |
| reasoning_output_tokens | xxx |

**按模型：**
| 模型 | total_tokens |
|------|-------------|
| gpt-x.x | xxx |

**最高消耗日期：**
| 日期 | total_tokens |
|------|-------------|
| ... | ... |

**异常提醒：**
- warning 数: N
- 如有 fork 检测到的异常 spike，说明位置和量级

**输出文件：** `~/Desktop/codex-ledger-report/`
```

## Safety / privacy

- Do **not** upload `~/.codex/sessions` logs to external services. Process locally only.
- Do **not** print full raw prompts or message content from rollout files unless the user explicitly asks.
- Usage analysis should focus on: token fields, model, timestamp, session_id, cwd, file path.

## Edge cases to handle

### No token_count events found

If the scan returns 0 events, check:
1. Is `~/.codex/sessions` populated?
2. Is the Codex CLI version recent enough?
3. Is `--since` date correct?

### Large spike on a single day

Read `raw_events.csv` for that day, check:
1. Are there suspicious fork events (total > 5M on first token_count)?
2. Are there negative delta warnings?
3. Is there a single rollout file dominating?

### Multiple accounts sharing one .codex

Explain: "检测到多个账号共用同一个 ~/.codex，工具无法按账号区分，以下为目录总量。"

## Script location

The main script is at the repository root:

```
codex-ledger/codex_usage_local.py
```

When installed as a skill, it may also be symlinked at:

```
skills/codex-ledger/scripts/codex_usage_local.py
```
