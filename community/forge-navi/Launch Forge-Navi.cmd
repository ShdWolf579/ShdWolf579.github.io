@echo off
setlocal
cd /d "%~dp0"

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0forge_navi_desktop.py"
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0forge_navi_desktop.py"
    exit /b %errorlevel%
)

echo Python 3 was not found.
echo Install Python 3 from python.org, then launch Forge-Navi again.
pause
