@echo off
rem Thin wrapper. The real logic lives in autostart_on.ps1 next to this file.
rem
rem History: this used to build the .vbs out of cmd echo lines, and the .vbs
rem ShellExecuted start_engine.bat directly. Windows shows "Open File - Security
rem Warning" for batch files that came out of a downloaded ZIP, so at logon the
rem engine never started and a dialog waited for a click instead.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

if not exist "%~dp0autostart_on.ps1" (
  echo [PAE] ERROR: autostart_on.ps1 is missing next to this file.
  echo        Re-extract the complete ZIP and run Install.cmd again.
  pause
  exit /b 1
)

set "PAE_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PAE_PS%" (
  echo [PAE] ERROR: Windows PowerShell was not found at:
  echo        %PAE_PS%
  pause
  exit /b 1
)

"%PAE_PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart_on.ps1"
set "PAE_RC=%errorlevel%"
echo.
if not "%PAE_RC%"=="0" (
  echo [PAE] Autostart was NOT enabled. Read the message above.
)
pause
exit /b %PAE_RC%
