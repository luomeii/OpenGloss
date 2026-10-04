@echo off
rem Start the OpenGloss engine on port 4815.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

rem Why this guard looks odd -- three cmd.exe traps:
rem  1) 'where python' is not usable: the Microsoft Store stub is a real
rem     python.exe that 'where' finds but which cannot run anything.
rem  2) exit codes are compared as STRINGS ("%errorlevel%"=="0"), never with
rem     'if errorlevel 1': a broken install (missing DLL) or a crashed child
rem     exits in the 0x8xxxxxxx range, i.e. NEGATIVE, and 'if errorlevel 1'
rem     does not catch those -- it would report success.
rem  3) no pipe and no nested 'exit /b': a failed pipeline aborted the script
rem     with a bare 255, and an 'exit /b N' that is not the last statement of
rem     its parenthesised block loses N (cmd parses the block first).
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

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $c = Get-NetTCPConnection -LocalPort 4815 -State Listen -ErrorAction SilentlyContinue; if ($c) { Write-Host ('[PAE] already running, PID ' + $c[0].OwningProcess) } else { Start-Process -WindowStyle Hidden python -ArgumentList '-m','uvicorn','pae_core.api:app','--port','4815' -WorkingDirectory (Get-Location).Path }"
if errorlevel 1 ( echo [PAE] ERROR: could not launch uvicorn. & pause & exit /b 1 )

rem Wait until the engine answers /v1/health AND reports THIS folder's pae.db.
rem /v1/health alone is not enough (it stays ok even when the engine is broken),
rem and any 200 is not enough either: a leftover engine using a different
rem database also answers 200, and the old check called that 'ready'.
rem First start loads a 65 MB dictionary (~25s, minutes when the disk is busy),
rem so the loop below can legitimately run for several minutes.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $mine = (Join-Path (Get-Location).Path 'pae.db'); $ok = $false; for ($i=0; $i -lt 45; $i++) { try { $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:4815/v1/health' -UseBasicParsing -TimeoutSec 10; $txt = [System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray()); $h = $txt | ConvertFrom-Json; if ($h.db -and ($h.db.ToString().ToLower() -eq $mine.ToLower())) { $ok = $true; break } } catch { } Start-Sleep -Seconds 2 }; if ($ok) { Write-Host '[PAE] engine ready: http://127.0.0.1:4815' } else { Write-Host '[PAE] ERROR: no engine using THIS folder''s pae.db answered (waited several minutes).'; Write-Host '        Another program may hold port 4815, or the engine failed to start.'; Write-Host '        Run it in the foreground to see why:  python -m uvicorn pae_core.api:app --port 4815'; exit 1 }"
if errorlevel 1 ( pause & exit /b 1 )
pause
exit /b 0
