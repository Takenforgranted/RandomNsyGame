@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=python"
if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"

echo [1/3] 使用解释器：%PY%
"%PY%" -c "import websockets, PIL" >nul 2>&1
if errorlevel 1 (
    echo [2/3] 缺少依赖，正在安装 requirements.txt ...
    "%PY%" -m pip install -r requirements.txt
) else (
    echo [2/3] 依赖已就绪
)

if not exist "config.json" (
    echo [提示] 未找到 config.json，已从 config.example.json 复制一份，请按需修改后重新运行。
    copy /y "config.example.json" "config.json" >nul
    pause
    exit /b 0
)

echo [3/3] 启动 QQ 机器人 ...
"%PY%" bot.py
pause
