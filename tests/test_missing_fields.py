#!/usr/bin/env python3
"""验证缺失字段容错：total_tokens/cached_input_tokens/reasoning_output_tokens 缺失不应整条跳过。

Fixture:
  - 事件 1: 缺 total_tokens、cached、reasoning → 推导 total=1500，其余默认 0
  - 事件 2: 缺 reasoning_output_tokens → 默认 0，正常 delta
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "missing_fields"
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
        # 事件 1: total 推导为 1500。事件 2: delta = 2000-2000=0?
        # 事件 1 total=1500 (input 1000+output 500), 事件 2 total 推导=3000 (input 2000+output 1000)
        # delta = 3000-1500 = 1500, total = 1500+1500 = 3000
        # Wait, there are only 2 events:
        # Event 1: first, delta={input:1000, output:500, total:1500(derived)}, prev_tokens set
        # Event 2: current={input:2000, cached:800, output:1000, total:3000(derived)}
        #   delta = 2000-1000=1000 input, 1000-500=500 output, 3000-1500=1500 total
        #   but cached: 800-0=800 (previous was 0 because missing field default)
        # So events: 2 events
        # total_tokens: 3000
        checks.append(("2 个增量事件", bool(re.search(r"有效 token_count 增量事件数:\s+2\b", output))))
        checks.append(("total_tokens 正确推导", bool(re.search(r"总 token \(total_tokens\):\s+3,?000\b", output))))

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
