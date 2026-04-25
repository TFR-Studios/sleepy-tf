@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 正在启动 Sleepy Server...
start "Sleepy Server" cmd /k "chcp 65001 >nul & python main.py"

echo 等待服务器启动...
timeout /t 5 /nobreak >nul

echo 启动 Sleepy Client...
chcp 65001 >nul
python sleepy_client.py

pause
