@echo off
setlocal
cd /d "%~dp0"

echo.
echo   SQLVIP - build Windows executable
echo   =================================
echo.

if not exist ".build-venv\Scripts\python.exe" (
  echo [1/3] Creating build environment...
  python -m venv --system-site-packages .build-venv
  if errorlevel 1 goto :error
)

echo [2/3] Installing build dependencies...
".build-venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 goto :error

echo [3/3] Building dist\SQLVIP.exe...
".build-venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm SQLVIP.spec
if errorlevel 1 goto :error

echo.
echo Build complete: %CD%\dist\SQLVIP.exe
echo.
exit /b 0

:error
echo.
echo Build failed. Check the output above.
pause
exit /b 1
