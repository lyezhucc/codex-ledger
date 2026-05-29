#!/usr/bin/env python3
"""验证普通负 delta 不 pop：只有上一条被标记为 suspicious baseline 才允许回溯移除。

Fixture:
  - 事件 1: total=1500 (正常，不 suspicious)
  - 事件 2: total=700  (负 delta -800，普通计数器重置)

期望：事件 1 保留，事件 2 用 700 作为新段增量。总量 = 1500 + 700 = 2200。
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "negative_delta_no_pop"
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
        checks.append(("2 个增量事件", bool(re.search(r"有效 token_count 增量事件数:\s+2\b", output))))
        # 总量 = 1500 + 700 = 2200（事件 1 未被 pop）
        checks.append(("total=2200 (事件1 未被 pop)", bool(re.search(r"总 token \(total_tokens\):\s+2,?200\b", output))))

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
