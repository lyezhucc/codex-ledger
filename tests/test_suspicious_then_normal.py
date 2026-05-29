#!/usr/bin/env python3
"""验证 suspicious→正常正delta→负delta 时不误pop事件2。

Fixture:
  - 事件 1: total=5,300,000 (>5M, suspicious) → appended, last_event_suspicious_baseline=True
  - 事件 2: total=5,301,000 (正常正 delta +1000) → appended, 标记清除为 False
  - 事件 3: total=700       (负 delta，last_event_suspicious_baseline=False)

期望：3 个增量事件，事件 2 不被 pop。
      总量 = 5,300,000 + 1,000 + 700 = 5,301,700。
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "suspicious_then_normal"
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
        # 3 个 token_count 都产生增量事件（事件 2 未被误 pop）
        checks.append(("3 个增量事件 (事件2 未被误 pop)", bool(re.search(r"有效 token_count 增量事件数:\s+3\b", output))))
        # 总量 = 5,300,000 + 1,000 + 700 = 5,301,700
        checks.append(("total=5,301,700", bool(re.search(r"总 token \(total_tokens\):\s+5,?301,?700\b", output))))
        # 不应出现只有 2 个事件的结果（如果事件2被误pop，total会不同）
        # 如果误pop: 5,300,000 + 700 = 5,300,700

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
