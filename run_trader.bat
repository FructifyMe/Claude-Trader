@echo off
REM ============================================================
REM Auto-Trader Bot Launcher for Windows
REM Double-click to start. Dashboard opens in your browser.
REM ============================================================

cd /d "%~dp0"

echo [%date% %time%] Bot launcher starting...

REM Check for Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] ERROR: Python not found in PATH!
    echo  Download from https://www.python.org/downloads/
    echo  IMPORTANT: Check "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM First-run setup
if not exist ".venv" (
    echo [%date% %time%] First-time setup — installing dependencies...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt -q
    echo [%date% %time%] Setup complete!
) else (
    call .venv\Scripts\activate.bat
)

REM Verify activation worked
python --version
if %errorlevel% neq 0 (
    echo [%date% %time%] ERROR: Python venv activation failed!
    exit /b 1
)

REM Create data dirs if missing
if not exist "data\daily_summary" mkdir data\daily_summary

echo [%date% %time%] Starting bot...

REM Only open dashboard if running interactively (not from Task Scheduler)
if "%1" neq "--background" (
    echo.
    echo  ============================================================
    echo   AUTO-TRADER BOT
    echo   Mode: Paper Trading
    echo   Dashboard: Opening in browser...
    echo   Logs: data\bot.log
    echo   Stop: Close this window or press Ctrl+C
    echo  ============================================================
    echo.
    start "" "dashboard.html"
)

REM Start the bot
python -m src.main
echo [%date% %time%] Bot exited with code %errorlevel%
