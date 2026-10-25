@echo off
rem Start the OpenGloss engine on port 4815, then wait until it answers with
rem the pae.db of THIS folder. Two false-success traps are avoided:
rem  - /v1/health alone is not enough (it stays ok even when the engine failed
rem    to build), and any 200 is not enough either: a leftover engine using a
rem    different database also answers 200 -- the old check called that ready.
rem  - if the port is held by an engine with another database, say so AT ONCE
rem    instead of polling silently for minutes.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

rem Why this guard looks odd -- see status_engine.bat NOTE 4 about quote parity,
rem and never write a bare percent sign in a .bat: cmd expands it as a variable.
set "PAE_PY="
python --version >nul 2>nul
if "%errorlevel%"=="0" set "PAE_PY=python"
if defined PAE_PY goto :pae_run
py --version >nul 2>nul
if "%errorlevel%"=="0" set "PAE_PY=py"
if defined PAE_PY goto :pae_run
echo [PAE] ERROR: python is not usable in this terminal.
echo        Install Python 3.10+ from python.org and CHECK "Add python.exe to PATH",
echo        then reopen this window and retry.
pause
exit /b 1

:pae_run

rem Validate this source instance public marker.
%PAE_PY% -B source_health.py --prepare >nul
if errorlevel 1 ( echo [PAE] ERROR: local instance marker is unavailable. & pause & exit /b 1 )
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $paeStarted=0; $paePort=4815; if ($env:PAE_ENGINE_PORT) { if (-not [int]::TryParse($env:PAE_ENGINE_PORT,[ref]$paePort) -or $paePort -lt 1 -or $paePort -gt 65535) { Write-Host '[PAE] ERROR: invalid PAE_ENGINE_PORT'; exit 1 } }; function Stop-OwnEngine { if ($paeStarted) { $cl = (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $paeStarted) -ErrorAction SilentlyContinue).CommandLine; if ($cl -and ($cl -match 'uvicorn') -and ($cl -match 'pae_core.api:app')) { Stop-Process -Id $paeStarted -Force -ErrorAction SilentlyContinue } } }; $c=Get-NetTCPConnection -LocalPort $paePort -State Listen -ErrorAction SilentlyContinue; if (-not $c) { Write-Host '[PAE] starting engine; first dictionary load can take a minute...'; $paeStarted=(Start-Process -PassThru -WindowStyle Hidden %PAE_PY% -ArgumentList '-m','uvicorn','pae_core.api:app','--host','127.0.0.1','--port',$paePort -WorkingDirectory (Get-Location).Path).Id } else { Write-Host ('[PAE] port already held by PID '+$c[0].OwningProcess+'; checking local instance identity...') }; $sw=[System.Diagnostics.Stopwatch]::StartNew(); $next=10; while ($sw.Elapsed.TotalSeconds -lt 120) { try { $txt= & %PAE_PY% -B source_health.py; $h=ConvertFrom-Json -InputObject ($txt -join ''); if ($h.error -and $h.error -ne 'engine_connection_error') { Write-Host ('[PAE] health check failed: '+$h.error); Stop-OwnEngine; exit 1 }; if ($h.instance_id) { if ($h.ok -and ($h.instance_id.ToString().ToLowerInvariant() -eq $h.expected_instance_id.ToString().ToLowerInvariant())) { Write-Host ('[PAE] engine ready: http://127.0.0.1:'+$paePort); exit 0 }; Write-Host '[PAE] ERROR: this port belongs to a different database instance'; Stop-OwnEngine; exit 1 } } catch { Write-Host '[PAE] health probe failed'; Stop-OwnEngine; exit 1 }; if ($sw.Elapsed.TotalSeconds -ge $next) { Write-Host ('[PAE] still waiting... '+[int]$sw.Elapsed.TotalSeconds+'s'); $next+=10 }; Start-Sleep -Seconds 2 }; Write-Host '[PAE] ERROR: this instance did not become ready within 120s'; Stop-OwnEngine; exit 1"
if errorlevel 1 ( pause & exit /b 1 )
pause
exit /b 0
