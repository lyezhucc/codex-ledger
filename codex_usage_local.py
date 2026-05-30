#!/usr/bin/env python3
"""codex-ledger —— Codex 本地 Token 使用量统计工具。

从 ~/.codex/sessions/ 读取 rollout-*.jsonl 文件，
用增量算法统计 token 消耗（按天/模型/账号），导出 CSV。

核心算法：
    total_token_usage 是会话累计值。在同一个 rollout 文件内，按时间顺序
    对相邻 token_count 事件做差值：delta = current - previous。
    delta == 0 → 跳过（重复广播）
    delta < 0  → 计数器重置，用 current 值作为增量
    delta > 0  → 正常增量

用法:
    python3 codex_usage_local.py --since 2026-05-03 --tz Asia/Shanghai ~/.codex
    python3 codex_usage_local.py --since 2026-05-03 ~/.codex ~/acct-b/.codex
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ── 常量 ──────────────────────────────────────────────────
TOKEN_FIELDS = [
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
]

# ── 定价数据（每 1M token 的美元价格，2026 年 5 月估算） ──
# 注意：Codex 是订阅制产品，不是 API 按量计费。下方 "Codex API" 是
# 按同级别 OpenAI API 模型价格估算，仅供对比参考，不代表实际账单。
PRICING = {
    "openai_api": {
        "label": "OpenAI API (GPT-5 同级)",
        "models": {
            "gpt-5.4":       {"input": 2.50, "cached_input": 1.25, "output": 10.00},
            "gpt-5.5":       {"input": 5.00, "cached_input": 2.50, "output": 20.00},
            "gpt-5.4-mini":  {"input": 0.30, "cached_input": 0.15, "output": 1.20},
            # fallback for unknown models
            "__default__":   {"input": 2.50, "cached_input": 1.25, "output": 10.00},
        },
    },
    "deepseek_v4_pro": {
        "label": "DeepSeek V4 Pro (旗舰)",
        "models": {
            # 实际公开价 (2026-05-22 永久降价75%后 per 1M tokens USD)
            # https://api-docs.deepseek.com/zh-cn/quick_start/pricing
            "__any__":       {"input": 0.435, "cached_input": 0.003625, "output": 0.87},
        },
    },
    "claude_opus": {
        "label": "Claude Opus 4.8 (API)",
        "models": {
            "__any__":       {"input": 15.00, "cached_input": 1.50, "output": 75.00},
        },
    },
    "claude_sonnet": {
        "label": "Claude Sonnet 4.6 (API)",
        "models": {
            "__any__":       {"input": 3.00, "cached_input": 0.30, "output": 15.00},
        },
    },
}


def get_model_price(provider_key: str, model: str) -> dict:
    """查询某供应商对某模型的价格（每 1M token USD）。"""
    provider = PRICING.get(provider_key, {})
    models = provider.get("models", {})
    if model in models:
        return models[model]
    if "__any__" in models:
        return models["__any__"]
    return models.get("__default__", {"input": 0, "cached_input": 0, "output": 0})


def calc_cost(tokens: Dict[str, int], provider_key: str, model: str) -> Dict[str, float]:
    """根据 token 量和供应商价格计算费用（USD）。"""
    price = get_model_price(provider_key, model)
    cached = tokens.get("cached_input_tokens", 0)
    uncached = tokens["input_tokens"] - cached
    cost_cached = (cached / 1_000_000) * price["cached_input"]
    cost_uncached = (uncached / 1_000_000) * price["input"]
    cost_output = (tokens["output_tokens"] / 1_000_000) * price["output"]
    return {
        "provider": provider_key,
        "model": model,
        "cost_cached_input": round(cost_cached, 2),
        "cost_uncached_input": round(cost_uncached, 2),
        "cost_output": round(cost_output, 2),
        "cost_total": round(cost_cached + cost_uncached + cost_output, 2),
    }


def build_pricing_table(
    daily_by_model: List[Dict[str, Any]],
    model_totals: List[Dict[str, Any]],
    grand: Dict[str, int],
) -> Dict[str, Any]:
    """按供应商计算费用汇总。"""
    result: Dict[str, Any] = {}

    for provider_key in PRICING:
        provider_label = PRICING[provider_key]["label"]

        # 按天+模型计算
        daily_rows = []
        grand_cost = 0.0
        for row in daily_by_model:
            cost = calc_cost(row, provider_key, row["model"])
            daily_rows.append({**row, **cost})
            grand_cost += cost["cost_total"]

        # 按模型汇总
        model_cost_rows = []
        for row in model_totals:
            cost = calc_cost(row, provider_key, row["model"])
            model_cost_rows.append({**row, **cost})

        # 总量
        grand_cost_row = calc_cost(grand, provider_key, list(
            PRICING[provider_key]["models"].keys()
        )[0] if "__any__" not in PRICING[provider_key]["models"] else "__any__")

        result[provider_key] = {
            "label": provider_label,
            "daily": daily_rows,
            "by_model": model_cost_rows,
            "grand_cost": round(grand_cost, 2),
            "grand_detail": grand_cost_row,
        }

    return result

# ── 工具函数 ──────────────────────────────────────────────


def expand_path(path: str) -> Path:
    """展开 ~ 和环境变量，返回绝对路径。"""
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def parse_iso_timestamp(ts: str) -> Optional[datetime]:
    """解析 ISO 8601 时间戳到 UTC datetime。支持 Z 后缀和 +00:00。"""
    if not ts:
        return None
    ts = ts.strip()
    # 处理 "Z" 后缀
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        # Python 3.10 的 fromisoformat 支持大部分 ISO 格式
        return datetime.fromisoformat(ts)
    except ValueError:
        # 尝试只取前 19 字符 (YYYY-MM-DDTHH:MM:SS)
        try:
            return datetime.fromisoformat(ts[:19] + "+00:00")
        except ValueError:
            return None


def tzinfo_from_name(tz_name: str) -> timezone:
    """根据时区名称返回 ZoneInfo 对象（正确处理 DST）。"""
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        print(
            f"错误: 未知时区 '{tz_name}'。请使用如 Asia/Shanghai、America/New_York 等有效 IANA 时区名。",
            file=sys.stderr,
        )
        sys.exit(1)


def find_model_recursive(obj: Any, depth: int = 0) -> Optional[str]:
    """递归查找 JSON 中的 model 字段。"""
    if depth > 10:
        return None
    if isinstance(obj, dict):
        if "model" in obj and isinstance(obj["model"], str) and obj["model"]:
            return obj["model"]
        for v in obj.values():
            result = find_model_recursive(v, depth + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_model_recursive(item, depth + 1)
            if result:
                return result
    return None


def account_name_from_path(sessions_dir: Path, base_dirs: List[Path]) -> str:
    """根据 sessions 目录路径推断账号名称。"""
    # 如果是 ~/.codex 或 ~/.codex/sessions
    if sessions_dir == expand_path("~/.codex"):
        return "default"
    if sessions_dir == expand_path("~/.codex/sessions"):
        return "default"
    # 如果是 ~/xxx/.codex 或 ~/xxx/.codex/sessions
    for bd in base_dirs:
        try:
            sessions_dir.relative_to(bd)
        except ValueError:
            continue
        # sessions_dir 在某个 base_dir 下
        # base_dir 一般是 ~/xxx/.codex 或 ~/xxx/.codex/sessions
        parent = bd
        # parent.parent == parent 意味着已到文件系统根目录（/ 的 parent 是 /）
        while parent != expand_path("~") and parent.parent != parent:
            if parent.name == ".codex":
                # parent 的父目录就是账号目录
                account_dir = parent.parent
                if account_dir == expand_path("~"):
                    return "default"
                return account_dir.name
            parent = parent.parent
        return bd.name
    # 默认用目录名
    return sessions_dir.name


def resolve_sessions_dir(raw_dir: str) -> Path:
    """将用户输入的目录解析为 sessions 目录。

    如果输入 ~/.codex，返回 ~/.codex/sessions。
    如果输入 ~/.codex/sessions，直接返回。
    """
    p = expand_path(raw_dir)
    if p.name == "sessions":
        return p
    sessions_path = p / "sessions"
    if sessions_path.is_dir():
        return sessions_path
    return p


def date_str_from_path(file_path: Path) -> Optional[str]:
    """从文件路径中提取日期（YYYY-MM-DD 格式）。"""
    parts = file_path.parts
    for i, part in enumerate(parts):
        if part == "sessions" and i + 3 < len(parts):
            # sessions/YYYY/MM/DD/rollout-*.jsonl
            y, m, d = parts[i + 1], parts[i + 2], parts[i + 3]
            if y.isdigit() and len(y) == 4 and m.isdigit() and d.isdigit():
                return f"{y}-{m}-{d}"
    return None


def extract_token_info(
    info: dict,
) -> Tuple[Optional[Dict[str, int]], int]:
    """从 payload.info 中提取 token 数据。缺失字段默认 0。

    返回: (token_dict_or_none, warning_count)
      - 仅当 total_token_usage 完全缺失时返回 None
      - total_tokens 缺失时尝试 input_tokens + output_tokens 推导
      - reasoning_output_tokens / cached_input_tokens 缺失默认 0
    """
    if info is None:
        return None, 0
    tu = info.get("total_token_usage")
    if not tu or not isinstance(tu, dict):
        return None, 0

    result: Dict[str, int] = {}
    warnings = 0

    for field in TOKEN_FIELDS:
        val = tu.get(field)
        if isinstance(val, (int, float)):
            result[field] = int(val)
        else:
            result[field] = 0  # 缺失字段默认 0，不跳过整条

    # total_tokens 为 0 但 input/output 有值时，尝试推导
    if result["total_tokens"] == 0 and (result["input_tokens"] > 0 or result["output_tokens"] > 0):
        derived = result["input_tokens"] + result["output_tokens"]
        if derived > 0:
            result["total_tokens"] = derived
            warnings += 1

    return result, warnings


# ── 核心统计逻辑 ──────────────────────────────────────────


def process_rollout_file(
    file_path: Path,
    account: str,
    tz: timezone,
    since_date: Optional[datetime],
    debug: bool = False,
    exclude_suspicious_first: bool = False,
) -> Tuple[List[Dict[str, Any]], int, List[Dict[str, Any]]]:
    """处理单个 rollout JSONL 文件，返回增量事件列表、warning 计数和 suspicious 事件列表。

    返回: (raw_events, warning_count, suspicious_events)
    """
    events: List[Dict[str, Any]] = []
    suspicious_events: List[Dict[str, Any]] = []
    warnings = 0
    prev_tokens: Optional[Dict[str, int]] = None
    current_model: str = "unknown"
    session_id: str = "unknown"
    cwd: str = "unknown"
    # 标记最近一次 append 的增量事件是否来自 suspicious inherited baseline。
    # 仅在 append suspicious 事件后置 True；append 普通事件后置 False。
    # 负 delta 只在该标记为 True 时允许回溯 pop。
    last_event_suspicious_baseline: bool = False

    # 启发式阈值：第一个 token_count 超过此值可能是 fork 继承的历史累计
    FORK_SUSPICIOUS_THRESHOLD = 5_000_000

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    warnings += 1
                    if debug:
                        print(f"  [WARN] 无法解析 JSON: {file_path}:{line_no}", file=sys.stderr)
                    continue

                event_type = obj.get("type", "")
                payload = obj.get("payload", {}) if isinstance(obj.get("payload"), dict) else {}

                # 每行都递归查找 model 字段，找到就更新
                model = find_model_recursive(obj)
                if model:
                    current_model = model

                # 提取 session_meta 信息
                if event_type == "session_meta":
                    sid = payload.get("id")
                    if sid:
                        session_id = str(sid)
                    c = payload.get("cwd")
                    if c:
                        cwd = str(c)

                # 处理 token_count 事件（只检查 payload.type，不强依赖外层 event 类型）
                if isinstance(payload, dict) and payload.get("type") == "token_count":
                    info = payload.get("info")

                    # info 为 null 或缺失，跳过
                    if info is None:
                        continue

                    current, extract_warns = extract_token_info(info)
                    warnings += extract_warns
                    if current is None:
                        continue

                    # 解析时间戳
                    ts_str = obj.get("timestamp", "")
                    ts = parse_iso_timestamp(ts_str)

                    # 时间戳解析失败，尝试从文件名解析
                    if ts is None:
                        if debug:
                            print(
                                f"  [WARN] 无法解析时间戳 '{ts_str}'，"
                                f"尝试从文件名推断: {file_path.name}",
                                file=sys.stderr,
                            )
                        # 文件名格式: rollout-YYYY-MM-DDTHH-MM-SS-*.jsonl
                        fname = file_path.name
                        try:
                            date_part = fname[8:18]  # YYYY-MM-DD
                            time_part = fname[19:27].replace("-", ":")  # HH:MM:SS
                            ts_str_fb = f"{date_part}T{time_part}+00:00"
                            ts = datetime.fromisoformat(ts_str_fb)
                        except (ValueError, IndexError):
                            warnings += 1
                            if debug:
                                print(
                                    f"  [WARN] 文件名也无法解析时间，"
                                    f"跳过此 token_count 事件: {file_path.name}",
                                    file=sys.stderr,
                                )
                            continue

                    # 过滤 since_date：即使事件在范围外，也要推进 prev_tokens
                    # 否则跨边界文件的第一条范围内事件会把完整累计值当增量（严重高估）
                    if since_date is not None:
                        ts_local = ts.astimezone(tz)
                        if ts_local.replace(hour=0, minute=0, second=0, microsecond=0) < since_date:
                            prev_tokens = current  # 保持基线，但不产生增量事件
                            continue

                    # 计算增量
                    if prev_tokens is None:
                        # 第一个 token_count 事件：通常 total 从 0 开始，直接作为增量。
                        # 但如果 total 超过阈值，可能是 fork 继承的历史累计值。
                        is_suspicious = (
                            current["total_tokens"] > FORK_SUSPICIOUS_THRESHOLD
                        )
                        if is_suspicious:
                            warnings += 1
                            # 生成日期字符串用于 suspicious 事件记录
                            ts_local_susp = ts.astimezone(tz)
                            date_str_susp = ts_local_susp.strftime("%Y-%m-%d")
                            suspicious_events.append({
                                "date": date_str_susp,
                                "timestamp": ts.isoformat(),
                                "account": account,
                                "model": current_model,
                                "session_id": session_id,
                                "cwd": cwd,
                                "file": str(file_path),
                                "total_tokens": current["total_tokens"],
                                "input_tokens": current["input_tokens"],
                                "output_tokens": current["output_tokens"],
                                "cached_input_tokens": current["cached_input_tokens"],
                                "reasoning_output_tokens": current["reasoning_output_tokens"],
                                "reason": "suspicious_inherited_first_baseline",
                            })
                            if debug:
                                print(
                                    f"  [WARN] 首个 token_count total={current['total_tokens']:,} "
                                    f"超过阈值 {FORK_SUSPICIOUS_THRESHOLD:,}，"
                                    f"标记为 suspicious inherited baseline: {file_path.name}",
                                    file=sys.stderr,
                                )
                            if exclude_suspicious_first:
                                # 官网对账模式：不将首条大累计值计入增量，仅作为基线
                                if debug:
                                    print(
                                        f"         --exclude-suspicious-first-baseline 已启用，"
                                        f"跳过此条增量，仅设为 prev_tokens 基线",
                                        file=sys.stderr,
                                    )
                                # 检查是否全为 0
                                if all(v == 0 for v in current.values()):
                                    prev_tokens = current
                                else:
                                    prev_tokens = current
                                continue
                        # 检查是否全为 0（初始占位事件）
                        if all(v == 0 for v in current.values()):
                            prev_tokens = current
                            continue
                        delta = dict(current)
                        # 记录本条增量是否来自 suspicious baseline（后续负 delta 回溯用）
                        last_event_suspicious_baseline = is_suspicious
                    else:
                        delta = {}
                        all_zero_delta = True
                        for field in TOKEN_FIELDS:
                            d = current[field] - prev_tokens[field]
                            delta[field] = d
                            if d != 0:
                                all_zero_delta = False

                        if all_zero_delta:
                            # delta 全为 0，重复广播，跳过
                            prev_tokens = current
                            continue

                        # 检查负数 delta（计数器重置）
                        if delta["total_tokens"] < 0:
                            warnings += 1
                            if last_event_suspicious_baseline:
                                # 只有上一条事件被明确标记为 suspicious inherited baseline
                                # 时，才允许回溯移除（它来自 fork 继承的历史值，不是真正消耗）
                                if debug:
                                    print(
                                        f"  [WARN] delta total_tokens={delta['total_tokens']:,} 为负，"
                                        f"上一条为 suspicious baseline (total={prev_tokens['total_tokens']:,})，"
                                        f"回溯移除并重置: {file_path.name}",
                                        file=sys.stderr,
                                    )
                                if events and events[-1]["file"] == str(file_path):
                                    popped = events.pop()
                                    if debug:
                                        print(
                                            f"      已移除 suspicious 事件: "
                                            f"total_tokens={popped['total_tokens']:,}",
                                            file=sys.stderr,
                                        )
                                delta = dict(current)
                                last_event_suspicious_baseline = False  # 已处理，重置标记
                            else:
                                # 普通负 delta：不删除上一条，只记录 warning，
                                # 并把 current 作为新一段的增量
                                if debug:
                                    print(
                                        f"  [WARN] delta total_tokens={delta['total_tokens']:,} 为负，"
                                        f"上一条非 suspicious，保留上一条并重置基线"
                                        f" (prev_total={prev_tokens['total_tokens']:,}, "
                                        f"current_total={current['total_tokens']:,}): "
                                        f"{file_path.name}",
                                        file=sys.stderr,
                                    )
                                delta = dict(current)
                        else:
                            # 正 delta：本条是普通增量事件，清除 suspicious 标记
                            last_event_suspicious_baseline = False

                    prev_tokens = current

                    # 生成日期字符串
                    ts_local = ts.astimezone(tz)
                    date_str = ts_local.strftime("%Y-%m-%d")

                    event = {
                        "date": date_str,
                        "timestamp": ts.isoformat(),
                        "account": account,
                        "model": current_model,
                        "session_id": session_id,
                        "cwd": cwd,
                        "file": str(file_path),
                    }
                    event.update(delta)
                    events.append(event)

    except OSError as e:
        warnings += 1
        if debug:
            print(f"  [WARN] 无法读取文件 {file_path}: {e}", file=sys.stderr)

    return events, warnings, suspicious_events


def scan_all_rollouts(
    sessions_dirs: List[Path],
    account_names: List[str],
    tz: timezone,
    since_date: Optional[datetime],
    debug: bool = False,
    fast_path_filter: bool = False,
    exclude_suspicious_first: bool = False,
) -> Dict[str, Any]:
    """扫描所有 rollout 文件，返回统计结果。

    默认全量扫描，按事件 timestamp 精确过滤。
    开启 fast_path_filter 时用路径日期粗过滤（可能漏长会话，需 >=14 天 buffer）。
    """
    FAST_PATH_BUFFER_DAYS = 14
    all_events: List[Dict[str, Any]] = []
    all_suspicious: List[Dict[str, Any]] = []
    total_files = 0
    total_warnings = 0

    # 先收集所有匹配的文件再扫描（用于进度显示）
    file_list: List[Tuple[Path, str]] = []
    for sessions_dir, account in zip(sessions_dirs, account_names):
        if not sessions_dir.is_dir():
            if debug:
                print(f"  [WARN] 目录不存在，跳过: {sessions_dir}", file=sys.stderr)
            continue

        for root, dirs, files in os.walk(sessions_dir):
            for fname in files:
                if not fname.startswith("rollout-") or not fname.endswith(".jsonl"):
                    continue

                file_path = Path(root) / fname

                # fast_path_filter 模式：用路径日期粗过滤（>=14 天 buffer）
                if fast_path_filter:
                    file_date = date_str_from_path(file_path)
                    if file_date and since_date is not None:
                        try:
                            fd = datetime.strptime(file_date, "%Y-%m-%d").replace(tzinfo=tz)
                            if fd < since_date - timedelta(days=FAST_PATH_BUFFER_DAYS):
                                continue
                        except ValueError:
                            pass

                file_list.append((file_path, account))

    # 按文件路径排序，确保扫描顺序确定
    file_list.sort(key=lambda x: str(x[0]))

    total_to_scan = len(file_list)
    # 每处理 5% 打印一次进度
    progress_step = max(1, total_to_scan // 20)

    if debug and total_to_scan > 0:
        print(f"找到 {total_to_scan} 个 rollout 文件", file=sys.stderr)

    for idx, (file_path, account) in enumerate(file_list):
        total_files += 1
        events, warns, suspicious = process_rollout_file(
            file_path, account, tz, since_date, debug=debug,
            exclude_suspicious_first=exclude_suspicious_first,
        )
        all_events.extend(events)
        all_suspicious.extend(suspicious)
        total_warnings += warns

        # 进度提示
        if debug and total_files % progress_step == 0:
            pct = total_files * 100 // total_to_scan
            print(
                f"  进度: {total_files}/{total_to_scan} ({pct}%) "
                f"已收集 {len(all_events):,} 个增量事件, "
                f"{total_warnings} 个 warning",
                file=sys.stderr,
            )

    return {
        "events": all_events,
        "suspicious_events": all_suspicious,
        "total_files": total_files,
        "total_warnings": total_warnings,
    }


# ── 汇总计算 ──────────────────────────────────────────────


def aggregate_daily_by_model(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 date + model 汇总。"""
    groups: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
        lambda: {f: 0 for f in TOKEN_FIELDS}
    )
    for e in events:
        key = (e["date"], e["model"])
        for f in TOKEN_FIELDS:
            groups[key][f] += e[f]
    rows = []
    for (date, model), totals in sorted(groups.items()):
        rows.append({"date": date, "model": model, **totals})
    return rows


def aggregate_daily_by_account_model(
    events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """按 date + account + model 汇总。"""
    groups: Dict[Tuple[str, str, str], Dict[str, int]] = defaultdict(
        lambda: {f: 0 for f in TOKEN_FIELDS}
    )
    for e in events:
        key = (e["date"], e["account"], e["model"])
        for f in TOKEN_FIELDS:
            groups[key][f] += e[f]
    rows = []
    for (date, account, model), totals in sorted(groups.items()):
        rows.append({"date": date, "account": account, "model": model, **totals})
    return rows


def aggregate_by_model(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 model 汇总。"""
    groups: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {f: 0 for f in TOKEN_FIELDS}
    )
    for e in events:
        for f in TOKEN_FIELDS:
            groups[e["model"]][f] += e[f]
    rows = []
    for model in sorted(groups):
        rows.append({"model": model, **groups[model]})
    return rows


def aggregate_by_account(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 account 汇总。"""
    groups: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {f: 0 for f in TOKEN_FIELDS}
    )
    for e in events:
        for f in TOKEN_FIELDS:
            groups[e["account"]][f] += e[f]
    rows = []
    for account in sorted(groups):
        rows.append({"account": account, **groups[account]})
    return rows


def aggregate_by_session(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 session（rollout 文件）汇总，按 total_tokens 降序排列。"""
    groups: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            f: 0 for f in TOKEN_FIELDS
        }
    )
    # 额外记录每个 session 的元信息
    meta: Dict[str, Dict[str, str]] = {}

    for e in events:
        fpath = e["file"]
        for f in TOKEN_FIELDS:
            groups[fpath][f] += e[f]
        if fpath not in meta:
            meta[fpath] = {
                "account": e["account"],
                "model": e["model"],
                "session_id": e["session_id"],
                "cwd": e["cwd"],
                "date": e["date"],
                "events": 0,
            }
        meta[fpath]["events"] += 1
        # 更新 model（取最后出现的）
        meta[fpath]["model"] = e["model"]
        # 更新 date（取最后出现的，即最新活动日期）
        meta[fpath]["date"] = e["date"]

    rows = []
    for fpath, totals in groups.items():
        m = meta.get(fpath, {})
        rows.append({
            "file": fpath,
            "account": m.get("account", "unknown"),
            "session_id": m.get("session_id", "unknown"),
            "model": m.get("model", "unknown"),
            "cwd": m.get("cwd", "unknown"),
            "date": m.get("date", "unknown"),
            "event_count": m.get("events", 0),
            **totals,
        })

    # 按 total_tokens 降序排列
    rows.sort(key=lambda r: -r["total_tokens"])
    return rows


def grand_total(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """计算总量。"""
    totals = {f: 0 for f in TOKEN_FIELDS}
    for e in events:
        for f in TOKEN_FIELDS:
            totals[f] += e[f]
    return totals


# ── CSV 输出 ──────────────────────────────────────────────


def write_csv(
    filepath: str, rows: List[Dict[str, Any]], fields: List[str]
) -> None:
    """写入 CSV 文件。"""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_csvs(
    output_dir: str,
    events: List[Dict[str, Any]],
    daily_model: List[Dict[str, Any]],
    daily_account_model: List[Dict[str, Any]],
    model_totals: List[Dict[str, Any]],
    account_totals: List[Dict[str, Any]],
    grand: Dict[str, int],
    pricing_data: Optional[Dict[str, Any]] = None,
    session_ranking: Optional[List[Dict[str, Any]]] = None,
    suspicious_events: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """导出所有 CSV 文件，返回文件路径列表。"""
    files_written = []

    # raw_events.csv
    raw_fields = [
        "date", "timestamp", "account", "model", "session_id", "cwd", "file",
    ] + TOKEN_FIELDS
    path = os.path.join(output_dir, "raw_events.csv")
    write_csv(path, events, raw_fields)
    files_written.append(path)

    # daily_by_model.csv
    dm_fields = ["date", "model"] + TOKEN_FIELDS
    path = os.path.join(output_dir, "daily_by_model.csv")
    write_csv(path, daily_model, dm_fields)
    files_written.append(path)

    # daily_by_account_model.csv
    dam_fields = ["date", "account", "model"] + TOKEN_FIELDS
    path = os.path.join(output_dir, "daily_by_account_model.csv")
    write_csv(path, daily_account_model, dam_fields)
    files_written.append(path)

    # model_total.csv
    mt_fields = ["model"] + TOKEN_FIELDS
    path = os.path.join(output_dir, "model_total.csv")
    write_csv(path, model_totals, mt_fields)
    files_written.append(path)

    # account_total.csv
    at_fields = ["account"] + TOKEN_FIELDS
    path = os.path.join(output_dir, "account_total.csv")
    write_csv(path, account_totals, at_fields)
    files_written.append(path)

    # grand_total.csv
    gt_fields = TOKEN_FIELDS
    path = os.path.join(output_dir, "grand_total.csv")
    write_csv(path, [grand], gt_fields)
    files_written.append(path)

    # suspicious_events.csv
    if suspicious_events:
        se_fields = [
            "date", "timestamp", "account", "model", "session_id", "cwd", "file",
        ] + TOKEN_FIELDS + ["reason"]
        path = os.path.join(output_dir, "suspicious_events.csv")
        write_csv(path, suspicious_events, se_fields)
        files_written.append(path)

    # session_ranking.csv
    if session_ranking:
        sr_fields = [
            "rank", "date", "account", "model", "session_id", "cwd", "file",
            "event_count",
        ] + TOKEN_FIELDS
        path = os.path.join(output_dir, "session_ranking.csv")
        # 添加排名序号
        ranked = []
        for i, row in enumerate(session_ranking, 1):
            ranked.append({"rank": i, **row})
        write_csv(path, ranked, sr_fields)
        files_written.append(path)

    # 定价 CSV
    if pricing_data:
        for provider_key, pd in pricing_data.items():
            # daily_by_model_pricing_{provider}.csv
            price_fields = [
                "date", "model",
                "input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_output_tokens", "total_tokens",
                "cost_cached_input", "cost_uncached_input", "cost_output", "cost_total",
            ]
            path = os.path.join(output_dir, f"daily_pricing_{provider_key}.csv")
            write_csv(path, pd["daily"], price_fields)
            files_written.append(path)

            # model_total_pricing_{provider}.csv
            mt_price_fields = [
                "model",
                "input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_output_tokens", "total_tokens",
                "cost_cached_input", "cost_uncached_input", "cost_output", "cost_total",
            ]
            path = os.path.join(output_dir, f"model_pricing_{provider_key}.csv")
            write_csv(path, pd["by_model"], mt_price_fields)
            files_written.append(path)

    return files_written


def export_json(output_dir: str, data: Dict[str, Any]) -> str:
    """导出 JSON 报告。"""
    path = os.path.join(output_dir, "report.json")

    def default_serializer(obj):
        """处理不可序列化对象。"""
        if isinstance(obj, (datetime,)):
            return obj.isoformat()
        return str(obj)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=default_serializer)
    return path


# ── 终端输出 ──────────────────────────────────────────────


def print_report(
    scan_result: Dict[str, Any],
    daily_model: List[Dict[str, Any]],
    model_totals: List[Dict[str, Any]],
    account_totals: List[Dict[str, Any]],
    grand: Dict[str, int],
    output_dir: str,
    files_written: List[str],
    json_path: Optional[str],
    pricing_data: Optional[Dict[str, Any]] = None,
    session_ranking: Optional[List[Dict[str, Any]]] = None,
    session_top_n: int = 30,
    suspicious_events: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """打印终端汇总报告。"""
    events = scan_result["events"]
    total_files = scan_result["total_files"]
    total_warnings = scan_result["total_warnings"]

    def fmt_num(n: int) -> str:
        return f"{n:,}"

    suspicious = scan_result.get("suspicious_events", []) or []
    suspicious_sum = sum(s["total_tokens"] for s in suspicious)

    print()
    print("=" * 68)
    print("  Codex Token 使用量统计报告")
    print("=" * 68)
    print()
    print(f"  扫描文件数:                    {total_files:,}")
    print(f"  有效 token_count 增量事件数:   {len(events):,}")
    print(f"  warning 数:                    {total_warnings:,}")
    if suspicious:
        print(f"  suspicious 事件数:             {len(suspicious):,}")
        print(f"  suspicious total_tokens 合计:  {fmt_num(suspicious_sum)}")
    print()
    print("  ── 总量 ──")
    print(f"  总 token (total_tokens):        {fmt_num(grand['total_tokens'])}")
    print(f"  input_tokens:                   {fmt_num(grand['input_tokens'])}")
    print(f"  cached_input_tokens:            {fmt_num(grand['cached_input_tokens'])}")
    print(f"  output_tokens:                  {fmt_num(grand['output_tokens'])}")
    print(f"  reasoning_output_tokens:        {fmt_num(grand['reasoning_output_tokens'])}")
    print()
    print(f"  非缓存输入+输出（仅参考，不等于官方账单）: {fmt_num(grand['input_tokens'] - grand['cached_input_tokens'] + grand['output_tokens'])}")
    print()

    # 按模型汇总
    if model_totals:
        print("  ── 按模型汇总 ──")
        print(f"  {'模型':<30} {'total_tokens':>15} {'input':>12} {'output':>10}")
        print(f"  {'-'*30} {'-'*15} {'-'*12} {'-'*10}")
        for row in model_totals:
            print(
                f"  {row['model']:<30} {fmt_num(row['total_tokens']):>15} "
                f"{fmt_num(row['input_tokens']):>12} {fmt_num(row['output_tokens']):>10}"
            )
        print()

    # 按天+模型汇总
    if daily_model:
        from collections import defaultdict
        day_totals: Dict[str, int] = defaultdict(int)
        for row in daily_model:
            day_totals[row["date"]] += row["total_tokens"]

        print("  ── 按天汇总 ──")
        print(f"  {'日期':<14} {'total_tokens':>15}")
        print(f"  {'-'*14} {'-'*15}")
        for date in sorted(day_totals):
            print(f"  {date:<14} {fmt_num(day_totals[date]):>15}")
        print()

    # 按账号汇总
    if account_totals:
        print("  ── 按账号/目录汇总 ──")
        print(f"  {'账号':<30} {'total_tokens':>15}")
        print(f"  {'-'*30} {'-'*15}")
        for row in account_totals:
            print(f"  {row['account']:<30} {fmt_num(row['total_tokens']):>15}")
        print()

    # 会话排名
    if session_ranking:
        top_n = min(session_top_n, len(session_ranking))
        total_sessions = len(session_ranking)
        top_total = sum(r["total_tokens"] for r in session_ranking[:top_n])
        top_pct = (top_total / grand["total_tokens"] * 100) if grand["total_tokens"] > 0 else 0

        print(f"  ── 会话 Token 排名 (Top {top_n}/{total_sessions}) ──")
        print(f"  {'排名':<4} {'总Token':>14} {'模型':<16} {'事件数':>7} {'日期':>12} {'工作目录'}")
        print(f"  {'-'*4} {'-'*14} {'-'*16} {'-'*7} {'-'*12} {'-'*40}")

        for i, row in enumerate(session_ranking[:top_n], 1):
            cwd_short = row.get("cwd", "?")
            # 简化路径显示
            home = os.path.expanduser("~")
            if cwd_short.startswith(home):
                cwd_short = "~" + cwd_short[len(home):]
            if len(cwd_short) > 42:
                cwd_short = "..." + cwd_short[-39:]

            print(
                f"  {i:<4} {fmt_num(row['total_tokens']):>14} "
                f"{row['model']:<16} {row['event_count']:>7} "
                f"{row['date']:>12} {cwd_short}"
            )

        print()
        print(f"  Top {top_n} 合计: {fmt_num(top_total)} tokens "
              f"(占总量 {top_pct:.1f}%)")
        print()

    # 定价换算
    if pricing_data:
        print()
        print("  ── 跨供应商价格换算（估算，仅供参考）──")

        # 每日明细示例（最新一天）
        if daily_model:
            from collections import defaultdict
            day_detail: Dict[str, Dict[str, int]] = defaultdict(
                lambda: {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
            )
            for row in daily_model:
                d = row["date"]
                day_detail[d]["input_tokens"] += row["input_tokens"]
                day_detail[d]["cached_input_tokens"] += row["cached_input_tokens"]
                day_detail[d]["output_tokens"] += row["output_tokens"]
                day_detail[d]["reasoning_output_tokens"] += row["reasoning_output_tokens"]

            latest_date = sorted(day_detail.keys())[-1]
            dd = day_detail[latest_date]
            uncached = dd["input_tokens"] - dd["cached_input_tokens"]
            print()
            print(f"  📅 {latest_date} 日明细：")
            print(f"    输入（命中缓存）: {fmt_num(dd['cached_input_tokens'])} tokens")
            print(f"    输入（未命中）:   {fmt_num(uncached)} tokens")
            print(f"    输出:             {fmt_num(dd['output_tokens'])} tokens")
            print(f"    推理输出:         {fmt_num(dd['reasoning_output_tokens'])} tokens")
            print()

        # 总费用对比表
        print(f"  {'供应商':<30} {'估算费用 (USD)':>18}")
        print(f"  {'-'*30} {'-'*18}")
        for provider_key, pd in pricing_data.items():
            label = pd["label"]
            cost = pd["grand_cost"]
            print(f"  {label:<30} ${cost:>17,.2f}")

        # 如果只有 default 账号，show 一下 DeepSeek 和 Claude vs OpenAI 的对比
        print()
        print("  💡 以上为估算值。Codex 是订阅制，不按 token 计费；")
        print("     API 价格按各供应商公开定价估算（2026 年 5 月）。")
        print("     详见: ~/Desktop/codex-ledger-report/daily_pricing_*.csv")
        print()

    # 输出文件
    print("  ── CSV 文件 ──")
    for fp in files_written:
        print(f"  {fp}")
    if json_path:
        print(f"  {json_path}")
    print()
    print("=" * 68)


# ── 入口 ──────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Codex 本地 Token 使用量统计工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 codex_usage_local.py --since 2026-05-03 ~/.codex
  python3 codex_usage_local.py --since 2026-05-03 --tz Asia/Shanghai ~/.codex ~/account-b/.codex
  python3 codex_usage_local.py --since 2026-05-03 --out ~/Desktop/report --json --debug ~/.codex
        """,
    )
    parser.add_argument(
        "directories",
        nargs="*",
        help="要扫描的 Codex 目录（默认 ~/.codex）",
    )
    parser.add_argument(
        "--since",
        type=str,
        default="2026-05-03",
        help="开始日期 YYYY-MM-DD（默认 2026-05-03）",
    )
    parser.add_argument(
        "--tz",
        type=str,
        default="Asia/Shanghai",
        help="统计时区（默认 Asia/Shanghai）",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="CSV 输出目录（默认当前目录下的 ./output）",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="额外输出 report.json",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="打印扫描详情和 warning",
    )
    parser.add_argument(
        "--fast-path-filter",
        action="store_true",
        default=False,
        help="开启路径日期粗过滤（>=14 天 buffer，可能漏长会话）",
    )
    parser.add_argument(
        "--pricing",
        action="store_true",
        default=False,
        help="输出跨供应商价格换算（OpenAI API / DeepSeek / Claude）",
    )
    parser.add_argument(
        "--by-session",
        action="store_true",
        default=False,
        help="按会话（rollout 文件）汇总排名，输出 session_ranking.csv",
    )
    parser.add_argument(
        "--session-top",
        type=int,
        default=30,
        help="终端显示的会话排名数量（默认 30）",
    )
    parser.add_argument(
        "--exclude-suspicious-first-baseline",
        action="store_true",
        default=False,
        help="排除首个 token_count 超过 500 万的继承历史累计值（官网对账模式）",
    )

    args = parser.parse_args()

    # 目录参数
    if not args.directories:
        args.directories = ["~/.codex"]

    # 时区
    tz = tzinfo_from_name(args.tz)

    # since 日期
    try:
        since_date = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=tz)
    except ValueError:
        print(f"错误: --since 格式应为 YYYY-MM-DD，收到: {args.since}", file=sys.stderr)
        sys.exit(1)

    # 输出目录
    if args.out:
        output_dir = os.path.expanduser(args.out)
    else:
        output_dir = os.path.join(os.getcwd(), "output")

    # 解析 sessions 目录
    sessions_dirs: List[Path] = []
    account_names: List[str] = []

    for raw in args.directories:
        sessions_dir = resolve_sessions_dir(raw)
        sessions_dirs.append(sessions_dir)

        # 推断账号名称
        raw_path = expand_path(raw)
        if raw_path == expand_path("~/.codex"):
            account_names.append("default")
        elif raw_path == expand_path("~/.codex/sessions"):
            account_names.append("default")
        else:
            account_names.append(account_name_from_path(sessions_dir, sessions_dirs))

    if args.debug:
        print(f"时区: {args.tz}", file=sys.stderr)
        print(f"起始日期: {args.since}", file=sys.stderr)
        print(f"输出目录: {output_dir}", file=sys.stderr)
        print(f"扫描目录:", file=sys.stderr)
        for sd, an in zip(sessions_dirs, account_names):
            print(f"  {sd}  →  账号: {an}", file=sys.stderr)

    # 扫描
    if args.debug:
        print("开始扫描...", file=sys.stderr)

    scan_result = scan_all_rollouts(
        sessions_dirs, account_names, tz, since_date,
        debug=args.debug,
        fast_path_filter=args.fast_path_filter,
        exclude_suspicious_first=args.exclude_suspicious_first_baseline,
    )

    events = scan_result["events"]
    suspicious_events = scan_result.get("suspicious_events", [])

    # 汇总
    daily_model = aggregate_daily_by_model(events)
    daily_account_model = aggregate_daily_by_account_model(events)
    model_totals = aggregate_by_model(events)
    account_totals = aggregate_by_account(events)
    grand = grand_total(events)

    # 会话排名（按 --by-session 或始终计算，因为便宜）
    session_ranking = aggregate_by_session(events) if args.by_session else None

    # 定价换算
    pricing_data: Optional[Dict[str, Any]] = None
    if args.pricing:
        pricing_data = build_pricing_table(daily_model, model_totals, grand)

    # 导出 CSV
    files_written = export_csvs(
        output_dir,
        events,
        daily_model,
        daily_account_model,
        model_totals,
        account_totals,
        grand,
        pricing_data,
        session_ranking,
        suspicious_events,
    )

    # 导出 JSON
    json_path: Optional[str] = None
    if args.json_output:
        suspicious_summary = {
            "total_suspicious_events": len(suspicious_events),
            "suspicious_total_tokens": sum(e["total_tokens"] for e in suspicious_events),
            "exclude_suspicious_first_baseline": args.exclude_suspicious_first_baseline,
        }
        report_data = {
            "config": {
                "since": args.since,
                "tz": args.tz,
                "directories": [str(sd) for sd in sessions_dirs],
                "exclude_suspicious_first_baseline": args.exclude_suspicious_first_baseline,
            },
            "summary": {
                "total_files": scan_result["total_files"],
                "total_events": len(events),
                "total_warnings": scan_result["total_warnings"],
                **suspicious_summary,
            },
            "grand_total": grand,
            "by_model": model_totals,
            "by_account": account_totals,
            "daily_by_model": daily_model,
            "daily_by_account_model": daily_account_model,
            "suspicious_events": suspicious_events,
            "raw_events": events,
        }
        if pricing_data:
            # 把 pricing 里不可序列化的 float 转好
            report_data["pricing"] = {
                k: {
                    "label": v["label"],
                    "grand_cost": v["grand_cost"],
                    "grand_detail": v["grand_detail"],
                    "by_model": v["by_model"],
                }
                for k, v in pricing_data.items()
            }
        json_path = export_json(output_dir, report_data)

    # 终端报告
    print_report(
        scan_result,
        daily_model,
        model_totals,
        account_totals,
        grand,
        output_dir,
        files_written,
        json_path,
        pricing_data,
        session_ranking,
        args.session_top,
        suspicious_events,
    )


if __name__ == "__main__":
    main()
