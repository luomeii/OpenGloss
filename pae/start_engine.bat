@echo off
rem Thin wrapper. The real logic lives in start_engine.ps1 next to this file:
rem it starts the engine with the BUNDLED python.exe (never PATH), disables any
rem system proxy for the loopback health check, and prints the engine log when
rem the engine does not come up.
rem
rem History: the previous version resolved 'python' from PATH and polled
rem /v1/health with Invoke-WebRequest. On a machine without Python installed,
rem or behind a system proxy, that produced a silent 120s timeout with no
rem diagnostic. Keep this file ASCII-only: cmd.exe reads .bat in the OEM page.
cd /d "%~dp0"

if not exist "%~dp0start_engine.ps1" (
  echo [PAE] ERROR: start_engine.ps1 is missing next to this file.
  echo        Re-extract the complete ZIP and run Install.cmd again.
  pause
  exit /b 1
)

rem Use the absolute PowerShell path: a trimmed PATH may not contain it.
set "PAE_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PAE_PS%" (
  echo [PAE] ERROR: Windows PowerShell was not found at:
  echo        %PAE_PS%
  pause
  exit /b 1
)

"%PAE_PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_engine.ps1" %*
set "PAE_RC=%errorlevel%"

if not "%PAE_RC%"=="0" (
  echo.
  pause
)
exit /b %PAE_RC%
