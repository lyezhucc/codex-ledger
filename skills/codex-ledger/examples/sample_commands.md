# Codex Ledger 常用命令示例

## 基本用法

```bash
# 默认扫描 ~/.codex，统计 2026-05-03 至今
python3 codex_usage_local.py

# 指定起始日期和时区
python3 codex_usage_local.py --since 2026-05-03 --tz Asia/Shanghai ~/.codex

# 输出 JSON 报告
python3 codex_usage_local.py --since 2026-05-03 --json ~/.codex

# debug 模式（打印扫描详情和 warning）
python3 codex_usage_local.py --since 2026-05-03 --debug ~/.codex

# 自定义输出目录
python3 codex_usage_local.py --since 2026-05-03 --out ~/Desktop/my-report ~/.codex
```

## 多账号

```bash
# 两个账号
python3 codex_usage_local.py \
  --since 2026-05-03 \
  ~/.codex \
  ~/codex-profiles/work/.codex

# 三个账号 + JSON
python3 codex_usage_local.py \
  --since 2026-05-03 \
  --tz Asia/Shanghai \
  --out ~/Desktop/codex-ledger-report \
  --json \
  ~/.codex \
  ~/codex-profiles/work/.codex \
  ~/codex-profiles/personal/.codex
```

## 加速模式（可能漏长会话）

```bash
python3 codex_usage_local.py --since 2026-05-03 --fast-path-filter ~/.codex
```

## 统计所有历史数据

```bash
python3 codex_usage_local.py --since 2020-01-01 ~/.codex
```

## 不同时区

```bash
# 美东
python3 codex_usage_local.py --since 2026-05-03 --tz America/New_York ~/.codex

# UTC
python3 codex_usage_local.py --since 2026-05-03 --tz UTC ~/.codex
```
