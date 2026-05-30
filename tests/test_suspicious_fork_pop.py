#!/usr/bin/env python3
"""验证 suspicious fork 时负 delta 回溯 pop：只有上一条被标记为 suspicious 才允许。

Fixture:
  - 事件 1: total=5,300,000 (>5M, suspicious inherited baseline)
  - 事件 2: total=700       (负 delta，触发回溯 pop 事件 1)
  - 事件 3: total=2,200     (正常增量 1500)

期望：事件 1 被 pop，剩 2 个事件。总量 = 700 + 1500 = 2200。
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "suspicious_fork_pop"
    tool = Path(__file__).resolve().parent.parent / "codex_usage_local.py"

    with tempfile.TemporaryDirectory(prefix="codex_test_") as tmpdir:
        result = subprocess.run(
            [sys.executable, str(tool), "--since", "2026-05-01",
             "--tz", "Asia/Shanghai", "--out", tmpdir, str(fixture_dir)],
            capture_output=True, text=True,
        )
        output = result.stdout + result.stderr
        checks = []

        checks.append(("returncode=0", result.returncode == 0))
        # 3 个 token_count，但事件 1 被 pop，剩 2 个增量事件
        checks.append(("2 个增量事件 (事件1 被 pop)", bool(re.search(r"有效 token_count 增量事件数:\s+2\b", output))))
        # 总量 = 700 + 1500 = 2200
        checks.append(("total=2200", bool(re.search(r"总 token \(total_tokens\):\s+2,?200\b", output))))
        # suspicious 已从增量中移除，总量不含 5,300,000
        checks.append(("总量行不含 5,300,000", not re.search(r"总 token \(total_tokens\).*5,?3[0-9]{2},?0{3}", output)))
        # suspicious 会在摘要行显示（用于排查），这是正确的
        checks.append(("suspicious 摘要存在", "suspicious total_tokens 合计:" in output))

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
