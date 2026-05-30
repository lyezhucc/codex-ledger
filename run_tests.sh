#!/bin/bash
# codex-ledger 一键测试
set -e

cd "$(dirname "$0")"

echo "=== 语法检查 ==="
python3 -m py_compile codex_usage_local.py tests/test_*.py
echo "OK"

echo ""
echo "=== 运行测试 ==="

tests=(
    "tests/test_sample.py"
    "tests/test_payload_type_only.py"
    "tests/test_missing_fields.py"
    "tests/test_negative_delta_no_pop.py"
    "tests/test_suspicious_fork_pop.py"
    "tests/test_suspicious_then_normal.py"
    "tests/test_since_boundary.py"
    "tests/test_exclude_suspicious_first_baseline.py"
)

passed=0
failed=0

for t in "${tests[@]}"; do
    echo "--- $(basename "$t") ---"
    if python3 "$t" 2>&1 | tail -1 | grep -q "🎉"; then
        echo "  ✅ PASS"
        ((passed++))
    else
        echo "  ❌ FAIL"
        ((failed++))
    fi
done

echo ""
echo "=============================="
echo "  结果: $passed 通过, $failed 失败"
echo "=============================="

if [ $failed -gt 0 ]; then
    exit 1
fi
