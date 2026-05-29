#!/bin/bash
# codex-ledger skill 安装脚本
# 将 skill 定义复制到 Codex 可识别的目录，使 Codex 能在对话中自动调用本工具。
set -e

# 环境检查
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/skills/codex-ledger"
SKILL_DEST="$HOME/.codex/skills/codex-ledger"

echo "📦 安装 codex-ledger skill..."

mkdir -p "$HOME/.codex/skills"

if [ -d "$SKILL_DEST" ]; then
    echo "   已存在旧版本，正在移除..."
    rm -rf "$SKILL_DEST"
fi

cp -R "$SKILL_SRC" "$SKILL_DEST"

echo ""
echo "✅ codex-ledger skill 已安装到:"
echo "   $SKILL_DEST"
echo ""
echo "现在你可以在 Codex 中直接说："
echo "   \"统计我从 5 月 3 日开始 Codex 用了多少 token，按模型和每天列出来\""
echo ""
echo "卸载："
echo "   rm -rf ~/.codex/skills/codex-ledger"
