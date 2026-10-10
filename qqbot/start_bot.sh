#!/usr/bin/env bash
# QQ 机器人一键启动脚本（Linux / macOS）
set -e
cd "$(dirname "$0")"

PY="python3"
if [ -x "../.venv/bin/python" ]; then
  PY="../.venv/bin/python"
fi

echo "[1/3] 使用解释器：$PY"
if ! "$PY" -c "import websockets, PIL" >/dev/null 2>&1; then
  echo "[2/3] 缺少依赖，正在安装 requirements.txt ..."
  "$PY" -m pip install -r requirements.txt
else
  echo "[2/3] 依赖已就绪"
fi

if [ ! -f "config.json" ]; then
  echo "[提示] 未找到 config.json，已从 config.example.json 复制一份，请按需修改后重新运行。"
  cp config.example.json config.json
  exit 0
fi

echo "[3/3] 启动 QQ 机器人 ..."
exec "$PY" bot.py
