@echo off
setlocal
rem Offline, in-place setup. No system Python or administrator is needed.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0distribution\install.ps1"
set "INSTALL_RESULT=%errorlevel%"
echo.
if not "%INSTALL_RESULT%"=="0" echo [OpenGloss] Installation failed. Read the message above.
pause
exit /b %INSTALL_RESULT%
