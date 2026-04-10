@echo off
REM Remove Auto-Trader from Windows Task Scheduler
REM Right-click → "Run as administrator"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  Right-click and choose "Run as administrator"
    pause
    exit /b 1
)

schtasks /delete /tn "AutoTrader_Start" /f 2>nul
schtasks /delete /tn "AutoTrader_Stop" /f 2>nul

echo.
echo  Auto-Trader removed from Task Scheduler.
echo  The bot files are still here — just won't auto-start anymore.
echo.
pause
