#!/usr/bin/env python3
"""测试 codex_usage_local.py 的样本数据，验证增量统计算法。

样本 fixture 内容：
  - 第 1 次 token_count: total=700  → 增量 700 (新会话起点)
  - 第 2 次 token_count: total=1800 → 增量 1100
  - 第 3 次 token_count: total=2700 → 增量 900
  - 第 4 次 token_count: total=2700 → 增量 0 (重复广播，跳过)
  - 第 5 次 token_count: info=null → 跳过

期望：
  - 有效增量事件数: 3
  - 总 total_tokens 增量: 700 + 1100 + 900 = 2700
  - 不是 700 + 1800 + 2700 = 5200 (错误直接相加)
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def run_tool(fixture_dir: Path, out_dir: str) -> subprocess.CompletedProcess:
    """用 --debug --out 运行主工具。"""
    tool = Path(__file__).resolve().parent.parent / "codex_usage_local.py"
    return subprocess.run(
        [
            sys.executable,
            str(tool),
            "--since", "2026-05-01",
            "--tz", "Asia/Shanghai",
            "--out", out_dir,
            "--debug",
            str(fixture_dir),
        ],
        capture_output=True,
        text=True,
    )


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent / "fixtures"

    with tempfile.TemporaryDirectory(prefix="codex_test_") as tmpdir:
        result = run_tool(fixture_dir, tmpdir)
        output = result.stdout + "\n" + result.stderr

        print("=" * 60)
        print("工具输出:")
        print(output)
        print("=" * 60)

        # 验证 returncode
        checks = []
        if result.returncode == 0:
            checks.append(("✅ returncode = 0", True))
        else:
            checks.append((f"❌ returncode = {result.returncode} (期望 0)", False))

        # 有效增量事件数应为 3
        if re.search(r"有效 token_count 增量事件数:\s+3\b", output):
            checks.append(("✅ 有效事件数 = 3", True))
        else:
            checks.append(("❌ 有效事件数不是 3", False))

        # 总 total_tokens 应为 2700
        if re.search(r"总 token \(total_tokens\):\s+2,?700\b", output):
            checks.append(("✅ 总量 = 2700", True))
        else:
            checks.append(("❌ 总量不是 2700（可能是直接相加了）", False))

        # 不应出现 5200（直接相加的错误结果）
        if re.search(r"5,?200", output):
            checks.append(("❌ 出现了 5200，可能是直接相加了", False))
        else:
            checks.append(("✅ 没有出现 5200（不是直接相加）", True))

        # 模型应为 gpt-5.1
        if "gpt-5.1" in output:
            checks.append(("✅ 正确识别模型 gpt-5.1", True))
        else:
            checks.append(("❌ 未找到模型 gpt-5.1", False))

        # 验证 CSV 已输出到临时目录（而非桌面）
        grand_csv = Path(tmpdir) / "grand_total.csv"
        if grand_csv.exists():
            content = grand_csv.read_text()
            if "2700" in content:
                checks.append(("✅ grand_total.csv 内容正确 (2700)", True))
            else:
                checks.append(("❌ grand_total.csv 内容不对", False))
        else:
            checks.append(("❌ grand_total.csv 未生成", False))

        print()
        print("检查结果:")
        all_ok = True
        for msg, ok in checks:
            print(f"  {msg}")
            if not ok:
                all_ok = False

        if all_ok:
            print(f"\n🎉 全部 {len(checks)} 项测试通过！（临时目录: {tmpdir}）")
        else:
            print("\n💥 部分测试失败，请检查输出。")
            sys.exit(1)


if __name__ == "__main__":
    main()
