#!/usr/bin/env python3
"""验证 --exclude-suspicious-first-baseline：跳过 fork 继承的首条大累计值。

Fixture:
  - 事件 1: total=5,300,000 (>5M, suspicious inherited baseline)
  - 事件 2: total=5,301,000 (delta=1000)
  - 事件 3: total=5,303,500 (delta=2500)

不开参数: total=5,303,500 (3 个事件)
开参数:   total=3,500 (2 个事件), suspicious_events.csv 有 1 行 5,300,000
"""

import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_tool(fixture_dir: Path, tmpdir: str, exclude: bool = False):
    """运行工具并返回 (stdout, 返回码)。"""
    tool = Path(__file__).resolve().parent.parent / "codex_usage_local.py"
    args = [
        sys.executable, str(tool),
        "--since", "2026-05-29",
        "--tz", "Asia/Shanghai",
        "--out", tmpdir,
        str(fixture_dir),
    ]
    if exclude:
        args.insert(4, "--exclude-suspicious-first-baseline")
    result = subprocess.run(args, capture_output=True, text=True)
    return result.stdout + result.stderr, result.returncode


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "exclude_suspicious_first"
    tool = Path(__file__).resolve().parent.parent / "codex_usage_local.py"

    all_ok = True

    # ── Test 1: 不开参数（raw 模式）──
    with tempfile.TemporaryDirectory(prefix="codex_test_raw_") as tmpdir:
        output, rc = run_tool(fixture_dir, tmpdir, exclude=False)
        checks = [
            ("raw: returncode=0", rc == 0),
            ("raw: 3 个增量事件", "有效 token_count 增量事件数:   3" in output),
            ("raw: total=5,303,500", "5,303,500" in output.replace(",", "") or "5303500" in output.replace(",", "")),
            ("raw: suspicious_events.csv 存在（有 suspicious 事件时总会导出）", os.path.exists(os.path.join(tmpdir, "suspicious_events.csv"))),
        ]
        for msg, ok in checks:
            print(f"  {'✅' if ok else '❌'} {msg}")
            if not ok:
                all_ok = False
        if any(not c[1] for c in checks):
            print("\n--- raw 模式输出 ---\n" + output[:2000])

    # ── Test 2: 开参数（对账模式）──
    with tempfile.TemporaryDirectory(prefix="codex_test_reconcile_") as tmpdir:
        output, rc = run_tool(fixture_dir, tmpdir, exclude=True)
        checks = [
            ("exclude: returncode=0", rc == 0),
            ("exclude: 2 个增量事件", "有效 token_count 增量事件数:   2" in output),
            ("exclude: total=3,500", "3,500" in output.replace(",", "") or "3500" in output.replace(",", "")),
            ("exclude: suspicious 事件数: 1", "suspicious 事件数:             1" in output),
            ("exclude: suspicious total 5,300,000", "5,300,000" in output.replace(",", "") or "5300000" in output.replace(",", "")),
        ]
        for msg, ok in checks:
            print(f"  {'✅' if ok else '❌'} {msg}")
            if not ok:
                all_ok = False
        if any(not c[1] for c in checks):
            print("\n--- 对账模式输出 ---\n" + output[:2000])

        # 验证 suspicious_events.csv
        se_path = os.path.join(tmpdir, "suspicious_events.csv")
        checks2 = [
            ("exclude: suspicious_events.csv 存在", os.path.exists(se_path)),
        ]
        if os.path.exists(se_path):
            with open(se_path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            checks2.append(("exclude: suspicious_events.csv 1 行", len(rows) == 1))
            if rows:
                checks2.append(("exclude: suspicious 行 total=5300000", int(rows[0]["total_tokens"]) == 5300000))
                checks2.append(("exclude: suspicious 行 reason 正确", "suspicious_inherited_first_baseline" in rows[0]["reason"]))
        for msg, ok in checks2:
            print(f"  {'✅' if ok else '❌'} {msg}")
            if not ok:
                all_ok = False

    if not all_ok:
        sys.exit(1)
    print(f"\n🎉 全部通过！")


if __name__ == "__main__":
    main()
