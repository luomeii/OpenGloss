@echo off
rem Stop the OpenGloss engine on port 4815.
rem
rem Safety: only kill the process if its command line really is our engine
rem (uvicorn pae_core.api:app). The earlier version force-killed whatever
rem happened to be listening on 4815, and still printed 'engine stopped'.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $c = Get-NetTCPConnection -LocalPort 4815 -State Listen -ErrorAction SilentlyContinue; if (-not $c) { Write-Host '[PAE] not running'; exit 0 }; $bad = 0; foreach ($x in $c) { $op = $x.OwningProcess; $cl = (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $op) -ErrorAction SilentlyContinue).CommandLine; if ($cl -and ($cl -match 'uvicorn') -and ($cl -match 'pae_core.api:app')) { Stop-Process -Id $op -Force -ErrorAction SilentlyContinue; Write-Host ('[PAE] engine stopped (PID ' + $op + ')') } else { $bad = 1; Write-Host ('[PAE] ERROR: port 4815 is held by PID ' + $op + ', which is NOT the OpenGloss engine.'); Write-Host ('        refusing to kill it. its command line: ' + $cl) } }; if ($bad -eq 1) { exit 1 }"
if errorlevel 1 (echo [PAE] stop did NOT complete & pause & exit /b 1)
pause
