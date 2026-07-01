@echo off
setlocal
cd /d "%~dp0"
python scripts\local-kit-launcher.py
if errorlevel 1 (
  py scripts\local-kit-launcher.py
)
