#!/usr/bin/env python3
"""验证 token_count 识别不强依赖外层 event type == "event_msg"。

Fixture: 外层 type="unknown_outer_type"，payload.type="token_count" 仍应被识别。
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "payload_type_only"
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
        # 应有 2 个增量事件（即使外层 type 不是 event_msg）
        checks.append(("2 个增量事件", bool(re.search(r"有效 token_count 增量事件数:\s+2\b", output))))
        # 总量: 800 + (1900-800) = 1900
        checks.append(("total=1900", bool(re.search(r"总 token \(total_tokens\):\s+1,?900\b", output))))

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
