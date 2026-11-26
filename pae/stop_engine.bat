@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_engine.ps1"
set "PAE_RC=%errorlevel%"
if not "%PAE_RC%"=="0" echo [PAE] Stop did not complete.
pause
exit /b %PAE_RC%
