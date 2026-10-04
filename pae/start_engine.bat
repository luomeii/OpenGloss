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

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $mine = (Join-Path (Get-Location).Path 'pae.db'); $c = Get-NetTCPConnection -LocalPort 4815 -State Listen -ErrorAction SilentlyContinue; if (-not $c) {   Write-Host '[PAE] starting the engine (first start loads a 65 MB dictionary, about 25s)...';   Start-Process -WindowStyle Hidden python -ArgumentList '-m','uvicorn','pae_core.api:app','--port','4815' -WorkingDirectory (Get-Location).Path } else {   Write-Host ('[PAE] port 4815 is already held by PID ' + $c[0].OwningProcess + ' - checking which database it uses...') }; $sw = [System.Diagnostics.Stopwatch]::StartNew(); $ok = $false; $other = ''; $next = 10; while ($sw.Elapsed.TotalSeconds -lt 120) {   try {     $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:4815/v1/health' -UseBasicParsing -TimeoutSec 8;     $txt = [System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray());     $h = ConvertFrom-Json -InputObject $txt;     if ($h.db -and ($h.db.ToString().ToLower() -eq $mine.ToLower())) { $ok = $true; break }     if ($h.db) { $other = $h.db; break }   } catch {     try {       if ([int]$_.Exception.Response.StatusCode -eq 503) {         try { $b = $_.ErrorDetails.Message } catch { }; try { $b = [System.Text.Encoding]::UTF8.GetString([System.Text.Encoding]::GetEncoding(28591).GetBytes($b)) } catch { };         Write-Host '[PAE] the engine started but is NOT healthy (it answered 503):';         Write-Host ('        ' + $b);         Write-Host '        Most often the dictionary is missing - complete README step 1 first.';         exit 1       }     } catch { }   };   if ($sw.Elapsed.TotalSeconds -ge $next) { Write-Host ('[PAE] still waiting... ' + [int]$sw.Elapsed.TotalSeconds + 's'); $next = $next + 10 }   Start-Sleep -Seconds 2 }; if ($ok) { Write-Host '[PAE] engine ready: http://127.0.0.1:4815'; exit 0 }; if ($other) {   Write-Host '[PAE] ERROR: port 4815 is held by an engine that is NOT using the pae.db of this folder.';   Write-Host ('        it reports db=' + $other);   Write-Host ('        this folder expects ' + $mine);   Write-Host '        Stop that one first (stop_engine.bat), or run this from the folder it belongs to.';   exit 1 }; Write-Host '[PAE] ERROR: nothing answered on 4815 with the pae.db of this folder within 120s.'; Write-Host '        Run it in the foreground to see why:  python -m uvicorn pae_core.api:app --port 4815'; exit 1"
if errorlevel 1 ( pause & exit /b 1 )
pause
exit /b 0
