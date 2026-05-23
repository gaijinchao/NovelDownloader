#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo ""
echo "  ═══════════════════════════════════════"
echo "    番茄小说 Web 下载器 - 正在启动..."
echo "  ═══════════════════════════════════════"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3，请先安装 Python 3.9+"
  exit 1
fi

PY="$ROOT/venv/bin/python"
PIP="$ROOT/venv/bin/pip"

if [[ ! -x "$PY" ]]; then
  echo "[1/4] 创建虚拟环境..."
  python3 -m venv "$ROOT/venv"
fi

if [[ ! -f "$ROOT/venv/.deps_ok" ]]; then
  echo "[2/4] 安装 Python 依赖..."
  "$PIP" install -r requirements.txt
  echo "[3/4] 安装 Playwright 浏览器组件..."
  "$PY" -m playwright install chromium
  echo ok > "$ROOT/venv/.deps_ok"
else
  echo "依赖已安装，跳过 pip / playwright 步骤。"
fi

echo "[4/4] 启动 Web 服务并打开浏览器..."
cd "$ROOT/src"
export FANQIE_OPEN_BROWSER=1
exec "$PY" server.py
