#!/usr/bin/env python3
"""验证 --since 边界：跨边界 rollout 文件的范围内事件正确使用基线增量。

Fixture (tz=Asia/Shanghai, since=2026-05-10):
  - 事件 1: 2026-05-09T15:00Z = 05-09 23:00+08 → 范围外，推进 prev_tokens
  - 事件 2: 2026-05-09T16:05Z = 05-10 00:05+08 → 范围内，delta=1700-700=1000
  - 事件 3: 2026-05-10T00:10Z = 05-10 08:10+08 → 范围内，delta=2900-1700=1200

期望: 2 个增量事件，总量 = 1000 + 1200 = 2200。
      (不是 1700 + 1200 = 2900)
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "since_boundary"
    tool = Path(__file__).resolve().parent.parent / "codex_usage_local.py"

    with tempfile.TemporaryDirectory(prefix="codex_test_") as tmpdir:
        result = subprocess.run(
            [sys.executable, str(tool), "--since", "2026-05-10",
             "--tz", "Asia/Shanghai", "--out", tmpdir, str(fixture_dir)],
            capture_output=True, text=True,
        )
        output = result.stdout + result.stderr
        checks = []

        checks.append(("returncode=0", result.returncode == 0))
        # 3 个 token_count，但事件 1 在范围外，剩 2 个增量事件
        checks.append(("2 个增量事件", bool(re.search(r"有效 token_count 增量事件数:\s+2\b", output))))
        # 总量 = 1000 + 1200 = 2200
        checks.append(("total=2200", bool(re.search(r"总 token \(total_tokens\):\s+2,?200\b", output))))
        # 不应出现 2900（直接相加的错误结果）
        checks.append(("不含 2900", "2,900" not in output and "2900" not in output.replace(",", "")))

        all_ok = True
        for msg, ok in checks:
            print(f"  {'✅' if ok else '❌'} {msg}")
            if not ok:
                all_ok = False
        if not all_ok:
            print("\n--- 工具输出 ---\n" + output)
            sys.exit(1)
        print(f"\n🎉 全部通过！")


if __name__ == "__main__":
    main()
