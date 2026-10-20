@echo off
setlocal
cd /d "%~dp0"
echo.
echo   SQLVIP - SQL dialect converter
echo   Starting on http://127.0.0.1:8000
echo.
python run.py
if errorlevel 1 pause
endlocal
