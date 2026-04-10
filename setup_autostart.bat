@echo off
REM ============================================================
REM ONE-TIME SETUP: Register Auto-Trader with Windows Task Scheduler
REM Right-click this file → "Run as administrator"
REM After this, the bot starts automatically every market morning.
REM You never touch it again.
REM ============================================================

echo.
echo  ============================================================
echo   AUTO-TRADER: Automatic Startup Setup
echo  ============================================================
echo.

REM Check for admin rights
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: This script needs Administrator rights.
    echo  Right-click setup_autostart.bat and choose "Run as administrator"
    echo.
    pause
    exit /b 1
)

REM Get the folder this script is in
set "BOTDIR=%~dp0"
set "BOTDIR=%BOTDIR:~0,-1%"

echo  Bot directory: %BOTDIR%
echo.

REM Delete old task if it exists (clean install)
schtasks /delete /tn "AutoTrader_Start" /f >nul 2>&1
schtasks /delete /tn "AutoTrader_Stop" /f >nul 2>&1

REM ── Task 1: Start bot at 9:20 AM ET every weekday ──
REM The bot's internal scheduler handles market hours (9:25 refresh, 9:45-3:45 trading)
echo  Creating startup task (weekdays 9:20 AM)...
schtasks /create ^
    /tn "AutoTrader_Start" ^
    /tr "wscript.exe \"%BOTDIR%\run_trader_background.vbs\"" ^
    /sc weekly /d MON,TUE,WED,THU,FRI ^
    /st 09:20 ^
    /rl HIGHEST ^
    /f

if %errorlevel% neq 0 (
    echo  ERROR: Failed to create startup task.
    pause
    exit /b 1
)

REM ── Task 2: Kill bot at 4:05 PM ET every weekday (market closed) ──
echo  Creating shutdown task (weekdays 4:05 PM)...

REM Create a small kill script
echo @echo off > "%BOTDIR%\stop_trader.bat"
echo taskkill /f /im python.exe /fi "WINDOWTITLE eq *auto-trader*" ^>nul 2^>^&1 >> "%BOTDIR%\stop_trader.bat"
echo taskkill /f /fi "MODULES eq src.main" ^>nul 2^>^&1 >> "%BOTDIR%\stop_trader.bat"

schtasks /create ^
    /tn "AutoTrader_Stop" ^
    /tr "cmd /c \"%BOTDIR%\stop_trader.bat\"" ^
    /sc weekly /d MON,TUE,WED,THU,FRI ^
    /st 16:05 ^
    /rl HIGHEST ^
    /f

if %errorlevel% neq 0 (
    echo  WARNING: Shutdown task failed. Bot will idle after hours (harmless).
)

echo.
echo  ============================================================
echo   DONE! Here's what happens now:
echo.
echo   Every weekday:
echo     9:20 AM  - Bot starts automatically
echo     9:25 AM  - Scans full market, builds watchlist
echo     9:45 AM  - Starts trading (scans every 5 min)
echo     3:45 PM  - Last scan cycle
echo     4:05 PM  - Bot shuts down
echo.
echo   You don't do anything. Check dashboard.html whenever
echo   you want to see what it's doing.
echo.
echo   To uninstall:
echo     Run "remove_autostart.bat" or delete tasks in
echo     Task Scheduler (search "AutoTrader")
echo  ============================================================
echo.
pause
