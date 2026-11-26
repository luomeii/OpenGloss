@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart_on.ps1" -Remove
set "PAE_RC=%errorlevel%"
if not "%PAE_RC%"=="0" echo [PAE] Autostart removal did not complete.
pause
exit /b %PAE_RC%
