@echo off
setlocal
cd /d "%~dp0"
if not defined NVIDIA_API_KEY (
  for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v NVIDIA_API_KEY 2^>nul') do set "NVIDIA_API_KEY=%%B"
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  python main.py
)
if errorlevel 1 pause
