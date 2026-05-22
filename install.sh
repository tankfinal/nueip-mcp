#!/bin/bash
# nueip-mcp installer — one-shot setup for macOS.
#
# Usage:
#   bash <(curl -fsSL https://raw.githubusercontent.com/tankfinal/nueip-mcp/main/install.sh)
#
# Or after manually cloning:
#   ./install.sh
#
# Override install dir:
#   NUEIP_MCP_DIR=/somewhere/else bash <(curl ...)

set -euo pipefail

DEFAULT_INSTALL_DIR="$HOME/nueip-mcp"
REPO_URL="https://github.com/tankfinal/nueip-mcp"

echo "═══ nueip-mcp 安裝精靈 ═══"
echo ""

# 1. Prereq: macOS (fail fast before any prompt — Linux/WSL users shouldn't bother typing)
if [[ "$(uname)" != "Darwin" ]]; then
  echo "❌ 這個 installer 只支援 macOS（用 Keychain 存密碼）。"
  echo "  Linux / WSL 請看 README 的 Troubleshooting 段落手動裝。"
  exit 1
fi
echo "✓ macOS"

# 2. Prereq: uv
if ! command -v uv >/dev/null 2>&1; then
  echo "❌ uv 未安裝。請先跑："
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "  裝完重開 terminal 再執行本腳本。"
  exit 1
fi
echo "✓ uv"

# 3. Prereq: Claude Code CLI
if ! command -v claude >/dev/null 2>&1; then
  echo "❌ Claude Code CLI (claude) 未安裝或不在 PATH。"
  echo "  下載 Claude Code: https://claude.com/code"
  exit 1
fi
echo "✓ claude CLI"
echo ""

# 4. Prereq: interactive TTY (process substitution 在 Claude Code 的 ! 模式 / IDE
#    內嵌 terminal / CI 會沒有 TTY，下面所有 read </dev/tty 都會炸)
if ! { : </dev/tty; } 2>/dev/null; then
  echo "❌ 偵測不到互動式 TTY，沒辦法問你員編 / 密碼。"
  echo ""
  echo "看起來你在 Claude Code 的 ! 模式、IDE 內嵌 terminal 或非互動 shell 跑這支腳本。"
  echo "請改開一個正常的 Terminal.app / iTerm 分頁，再執行："
  echo ""
  echo "    bash <(curl -fsSL https://raw.githubusercontent.com/tankfinal/nueip-mcp/main/install.sh)"
  echo ""
  exit 1
fi

# 5. Resolve install dir: NUEIP_MCP_DIR env var > interactive prompt > default
if [[ -n "${NUEIP_MCP_DIR:-}" ]]; then
  INSTALL_DIR="$NUEIP_MCP_DIR"
  echo "→ 使用 NUEIP_MCP_DIR 環境變數：$INSTALL_DIR"
else
  read -rp "要裝在哪？[預設 $DEFAULT_INSTALL_DIR]：" INSTALL_INPUT </dev/tty
  if [[ -z "$INSTALL_INPUT" ]]; then
    INSTALL_DIR="$DEFAULT_INSTALL_DIR"
  else
    # Expand leading ~ (read doesn't expand it for us)
    INSTALL_DIR="${INSTALL_INPUT/#\~/$HOME}"
  fi
fi
echo "→ 安裝位置：$INSTALL_DIR"
echo ""

# 6. Clone or update repo
if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "✓ $INSTALL_DIR 已存在，拉最新"
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "→ Clone 到 $INSTALL_DIR"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

# 7. uv sync
echo "→ 安裝 Python 依賴 (uv sync)"
(cd "$INSTALL_DIR" && uv sync --quiet)

# 8. chmod launch.sh
chmod +x "$INSTALL_DIR/launch.sh"
echo "✓ launch.sh executable"

# 9. Keychain password
if security find-generic-password -s nueip -a "$USER" > /dev/null 2>&1; then
  echo "✓ Keychain 已有 nueip 密碼（跳過；要改就先 security delete-generic-password -s nueip -a \"\$USER\" 再重跑）"
else
  echo ""
  echo "→ 請輸入你的 NUEiP 密碼（畫面不會回顯也不留 shell history）"
  security add-generic-password -s nueip -a "$USER" -U -w
  echo "✓ 密碼已存進 Keychain"
fi

# 10. Prompt for account
echo ""
read -rp "NUEiP 員編：" ACCOUNT </dev/tty
if [[ -z "$ACCOUNT" ]]; then
  echo "❌ 員編不能空，中止"
  exit 1
fi

# 11. Prompt for company subdomain
echo "（公司簡碼 = 你 NUEiP 登入頁 URL 的子網域，例：portal.nueip.com/login?ondemand=acme → acme）"
read -rp "公司簡碼：" COMPANY </dev/tty
if [[ -z "$COMPANY" ]]; then
  echo "❌ 公司簡碼不能空，中止"
  exit 1
fi

# 12. Register with Claude Code
echo "→ 註冊到 Claude Code (claude mcp add)"
claude mcp remove nueip 2>/dev/null || true
claude mcp add nueip \
  --env "NUEIP_COMPANY=$COMPANY" \
  --env "NUEIP_ACCOUNT=$ACCOUNT" \
  -- "$INSTALL_DIR/launch.sh"

echo ""
echo "═══ ✅ 安裝完成 ═══"
echo ""
echo "下一步："
echo "  1. 完全 quit Claude Code (cmd+Q) 再重開（mcp config 是 startup 時讀的）"
echo "  2. 在 Claude Code 內試問：「用 nueip 查我是誰」"
echo "  3. 想要更好用的 wrapper Skill 入口（/toolkit-pub:nueip）："
echo "     https://github.com/tankfinal/agent-skills-pub#quick-start"
echo ""
