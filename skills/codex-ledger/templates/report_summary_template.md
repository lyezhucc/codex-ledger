## Codex Token 使用量统计

**时间范围：** {since} 至今（{tz}）
**扫描文件数：** {total_files}
**有效增量事件数：** {total_events}
**Warning 数：** {total_warnings}

---

### 总量

| 指标 | 数值 |
|------|------|
| total_tokens | {grand_total_tokens} |
| input_tokens | {grand_input_tokens} |
| cached_input_tokens | {grand_cached_input_tokens} |
| output_tokens | {grand_output_tokens} |
| reasoning_output_tokens | {grand_reasoning_output_tokens} |
| 非缓存输入+输出（仅参考） | {non_cached_consumption} |

### 按模型

| 模型 | total_tokens | input_tokens | output_tokens |
|------|-------------|-------------|--------------|
{model_rows}

### 最高消耗日期 (Top 5)

| 日期 | total_tokens |
|------|-------------|
{top_days}

### 按账号

{account_rows}

### 异常提醒

{warnings_summary}

### 已知限制

- Codex Web/App/远程任务如未写入本机日志，统计不到
- Fork/resume 处理是启发式的，如需精确数据请与 OpenAI Usage Dashboard 交叉校验
- 如果启用了 `--fast-path-filter`，可能漏长会话

### 输出文件

```
{output_dir}/
├── grand_total.csv
├── model_total.csv
├── daily_by_model.csv
├── daily_by_account_model.csv
├── account_total.csv
├── raw_events.csv
└── report.json
```
