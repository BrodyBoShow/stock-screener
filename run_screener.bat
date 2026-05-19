@echo off
REM Double-click this file to run the screener.
cd /d "%~dp0"

echo Installing/updating dependencies...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

echo.
echo Running screener...
python screener.py
if errorlevel 1 (
    echo.
    echo ERROR: screener.py failed. See messages above.
    pause
    exit /b 1
)

echo.
echo Done. Excel dashboard should have opened automatically.
timeout /t 5 >nul
