@echo off
setlocal
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo.
echo Installation complete. Double-click run.bat to start Invoice Verifier.
pause
exit /b 0
:error
echo.
echo Installation failed. Review the error above.
pause
exit /b 1
