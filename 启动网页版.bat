@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ==========================================
echo   趣味猜女声优器（网页版） 正在启动...
echo   浏览器将自动打开 http://127.0.0.1:5000
echo   关闭本窗口即可停止游戏服务
echo  ==========================================
echo.
python web_app.py
pause
