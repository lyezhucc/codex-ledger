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
) -> Optional[Dict[str, int]]:
    """从 payload.info 中提取 token 数据。"""
    if info is None:
        return None
    tu = info.get("total_token_usage")
    if not tu or not isinstance(tu, dict):
        return None
    result = {}
    for field in TOKEN_FIELDS:
        val = tu.get(field)
        if isinstance(val, (int, float)):
            result[field] = int(val)
        else:
            return None  # 缺少必要字段
    return result


# ── 核心统计逻辑 ──────────────────────────────────────────


def process_rollout_file(
    file_path: Path,
    account: str,
    tz: timezone,
    since_date: Optional[datetime],
    debug: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    """处理单个 rollout JSONL 文件，返回增量事件列表和 warning 计数。

    返回: (raw_events, warning_count)
    """
    events: List[Dict[str, Any]] = []
    warnings = 0
    prev_tokens: Optional[Dict[str, int]] = None
    current_model: str = "unknown"
    session_id: str = "unknown"
    cwd: str = "unknown"

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

                # 追踪 model（从 turn_context 或 session_meta 中提取）
                if event_type in ("turn_context", "session_meta"):
                    model = find_model_recursive(payload)
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

                # 处理 token_count 事件
                if event_type == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info")

                    # info 为 null 或缺失，跳过
                    if info is None:
                        continue

                    current = extract_token_info(info)
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
                        # 但 fork session 可能继承历史累计值（几百万起步）。
                        if current["total_tokens"] > FORK_SUSPICIOUS_THRESHOLD:
                            warnings += 1
                            if debug:
                                print(
                                    f"  [WARN] 首个 token_count total={current['total_tokens']:,} "
                                    f"超过阈值 {FORK_SUSPICIOUS_THRESHOLD:,}，"
                                    f"可能是 fork/resume 继承的历史值: {file_path.name}",
                                    file=sys.stderr,
                                )
                        # 检查是否全为 0（初始占位事件）
                        if all(v == 0 for v in current.values()):
                            prev_tokens = current
                            continue
                        delta = dict(current)
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

                        # 检查负数 delta（计数器重置，典型的 fork 特征）
                        if delta["total_tokens"] < 0:
                            warnings += 1
                            if debug:
                                print(
                                    f"  [WARN] delta total_tokens={delta['total_tokens']:,} 为负，"
                                    f"视为 fork 计数器重置。回溯移除上一个错误增量"
                                    f"(total={prev_tokens['total_tokens']:,})，"
                                    f"用当前值重新开始: {file_path.name}",
                                    file=sys.stderr,
                                )
                            # 回溯：移除上一个事件（来自 fork 继承的历史值，不是真正消耗）
                            if events and events[-1]["file"] == str(file_path):
                                popped = events.pop()
                                if debug:
                                    print(
                                        f"      已移除错误事件: "
                                        f"total_tokens={popped['total_tokens']:,}",
                                        file=sys.stderr,
                                    )
                            # 用当前值作为新一段的起点（增量）
                            delta = dict(current)

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

    return events, warnings


def scan_all_rollouts(
    sessions_dirs: List[Path],
    account_names: List[str],
    tz: timezone,
    since_date: Optional[datetime],
    debug: bool = False,
) -> Dict[str, Any]:
    """扫描所有 rollout 文件，返回统计结果。"""
    all_events: List[Dict[str, Any]] = []
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

                # 粗过滤：路径中的日期比 since_date 早 1 天以上才跳过
                # 多保留 1 天的余量，防止午夜边界丢失事件
                file_date = date_str_from_path(file_path)
                if file_date and since_date is not None:
                    try:
                        fd = datetime.strptime(file_date, "%Y-%m-%d").replace(tzinfo=tz)
                        # 保留 since_date 前一天的文件（午夜边界安全余量）
                        if fd < since_date - timedelta(days=1):
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
        # 用文件路径中的日期做第二层粗过滤
        file_date = date_str_from_path(file_path)
        if file_date and since_date is not None:
            try:
                fd = datetime.strptime(file_date, "%Y-%m-%d").replace(tzinfo=tz)
                if fd < since_date:
                    # 前一天的文件：仍扫描但只保留 since_date 之后的事件（午夜安全）
                    pass  # 不跳过，让 process_rollout_file 按事件时间戳精确过滤
            except ValueError:
                pass

        total_files += 1
        events, warns = process_rollout_file(
            file_path, account, tz, since_date, debug=debug
        )
        all_events.extend(events)
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
) -> None:
    """打印终端汇总报告。"""
    events = scan_result["events"]
    total_files = scan_result["total_files"]
    total_warnings = scan_result["total_warnings"]

    def fmt_num(n: int) -> str:
        return f"{n:,}"

    print()
    print("=" * 68)
    print("  Codex Token 使用量统计报告")
    print("=" * 68)
    print()
    print(f"  扫描文件数:                    {total_files:,}")
    print(f"  有效 token_count 增量事件数:   {len(events):,}")
    print(f"  warning 数:                    {total_warnings:,}")
    print()
    print("  ── 总量 ──")
    print(f"  总 token (total_tokens):        {fmt_num(grand['total_tokens'])}")
    print(f"  input_tokens:                   {fmt_num(grand['input_tokens'])}")
    print(f"  cached_input_tokens:            {fmt_num(grand['cached_input_tokens'])}")
    print(f"  output_tokens:                  {fmt_num(grand['output_tokens'])}")
    print(f"  reasoning_output_tokens:        {fmt_num(grand['reasoning_output_tokens'])}")
    print()
    print(f"  实际消耗 (input - cached + output): {fmt_num(grand['input_tokens'] - grand['cached_input_tokens'] + grand['output_tokens'])}")
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
        help="CSV 输出目录（默认 ~/Desktop/codex-ledger-report）",
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
        output_dir = os.path.expanduser("~/Desktop/codex-ledger-report")

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
        sessions_dirs, account_names, tz, since_date, debug=args.debug
    )

    events = scan_result["events"]

    # 汇总
    daily_model = aggregate_daily_by_model(events)
    daily_account_model = aggregate_daily_by_account_model(events)
    model_totals = aggregate_by_model(events)
    account_totals = aggregate_by_account(events)
    grand = grand_total(events)

    # 导出 CSV
    files_written = export_csvs(
        output_dir,
        events,
        daily_model,
        daily_account_model,
        model_totals,
        account_totals,
        grand,
    )

    # 导出 JSON
    json_path: Optional[str] = None
    if args.json_output:
        report_data = {
            "config": {
                "since": args.since,
                "tz": args.tz,
                "directories": [str(sd) for sd in sessions_dirs],
            },
            "summary": {
                "total_files": scan_result["total_files"],
                "total_events": len(events),
                "total_warnings": scan_result["total_warnings"],
            },
            "grand_total": grand,
            "by_model": model_totals,
            "by_account": account_totals,
            "daily_by_model": daily_model,
            "daily_by_account_model": daily_account_model,
            "raw_events": events,
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
    )


if __name__ == "__main__":
    main()
