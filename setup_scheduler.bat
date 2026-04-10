@echo off
REM ============================================================
REM AUTO-TRADER: Set up daily auto-start
REM Right-click this file -> Run as Administrator
REM Only need to run this ONCE.
REM ============================================================

cd /d "%~dp0"
set BOT_DIR=%cd%

echo.
echo  ============================================================
echo   AUTO-TRADER SCHEDULER SETUP
echo  ============================================================
echo.

REM Check admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Must run as Administrator!
    echo  Right-click this file and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

REM Check Python first
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found!
    echo  Download from https://www.python.org/downloads/
    echo  IMPORTANT: Check "Add Python to PATH" during install.
    echo  Then run this again.
    echo.
    pause
    exit /b 1
)

echo  [1/4] Setting up Python environment...
if not exist ".venv" (
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt -q
    echo        Done - dependencies installed.
) else (
    echo        Already set up.
)

echo  [2/4] Creating data directories...
if not exist "data\daily_summary" mkdir data\daily_summary
echo        Done.

echo  [3/4] Creating scheduled tasks...

REM Delete existing tasks if they exist
schtasks /delete /tn "AutoTrader-Start" /f >nul 2>&1
schtasks /delete /tn "AutoTrader-Stop" /f >nul 2>&1

REM Create start task: weekdays at 9:20 AM
schtasks /create /tn "AutoTrader-Start" ^
  /tr "wscript.exe \"%BOT_DIR%\run_trader_background.vbs\"" ^
  /sc weekly /d MON,TUE,WED,THU,FRI ^
  /st 09:20 ^
  /rl HIGHEST ^
  /f >nul 2>&1

if %errorlevel% equ 0 (
    echo        Start task: weekdays at 9:20 AM [OK]
) else (
    echo        Start task [FAILED]
)

REM Create stop task: weekdays at 4:05 PM
schtasks /create /tn "AutoTrader-Stop" ^
  /tr "cmd /c taskkill /f /im python.exe /fi \"WINDOWTITLE eq *src.main*\"" ^
  /sc weekly /d MON,TUE,WED,THU,FRI ^
  /st 16:05 ^
  /rl HIGHEST ^
  /f >nul 2>&1

if %errorlevel% equ 0 (
    echo        Stop task: weekdays at 4:05 PM [OK]
) else (
    echo        Stop task [FAILED]
)

echo  [4/4] Generating initial dashboard...
call .venv\Scripts\activate.bat
python -c "import sys; sys.path.insert(0,'.'); from src.dashboard import generate_dashboard; generate_dashboard({'equity':100000,'cash':100000,'buying_power':200000,'portfolio_value':100000},[], bot_status='Idle - Waiting for market open')" 2>nul
echo        Done.

echo.
echo  ============================================================
echo   SETUP COMPLETE!
echo  ============================================================
echo.
echo   The bot will auto-start every weekday at 9:20 AM ET
echo   and stop at 4:05 PM ET.
echo.
echo   To start RIGHT NOW:  double-click run_trader.bat
echo   To check dashboard:  open dashboard.html in your browser
echo   To check logs:       open data\bot.log in Notepad
echo   To stop the bot:     close the bot window or run:
echo                         taskkill /f /im python.exe
echo.
echo   To REMOVE auto-start, run these in an admin CMD:
echo     schtasks /delete /tn "AutoTrader-Start" /f
echo     schtasks /delete /tn "AutoTrader-Stop" /f
echo.
pause
