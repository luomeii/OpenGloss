@echo off
rem Start the OpenGloss engine on port 4815.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

rem Guard: a missing python, or the Microsoft Store python stub, must fail loudly.
rem (where python is fooled by the stub -- it is a real python.exe that exits 49 --
rem  but python --version is not.)
python --version >nul 2>nul
if errorlevel 1 (
  echo [PAE] ERROR: python is not usable in this terminal.
  echo        Install Python 3.10+ from python.org and CHECK "Add python.exe to PATH",
  echo        then reopen this window and retry.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $c = Get-NetTCPConnection -LocalPort 4815 -State Listen -ErrorAction SilentlyContinue; if ($c) { Write-Host ('[PAE] already running, PID ' + $c[0].OwningProcess) } else { Start-Process -WindowStyle Hidden python -ArgumentList '-m','uvicorn','pae_core.api:app','--port','4815' -WorkingDirectory (Get-Location).Path }"
if errorlevel 1 ( echo [PAE] ERROR: could not launch uvicorn. & pause & exit /b 1 )

rem Wait until the engine really answers /v1/status. /v1/health alone is not enough:
rem it stays ok even when the engine failed to build (missing or corrupt dictionary).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $ok=$false; for ($i=0; $i -lt 45; $i++) { Start-Sleep -Seconds 2; try { $r = Invoke-RestMethod 'http://127.0.0.1:4815/v1/status' -TimeoutSec 5; if ($r) { $ok=$true; break } } catch {} }; if ($ok) { Write-Host '[PAE] engine ready: http://127.0.0.1:4815' } else { Write-Host '[PAE] ERROR: engine did not become ready within 90s.'; Write-Host '        Run it in the foreground to see why:  python -m uvicorn pae_core.api:app --port 4815'; exit 1 }"
if errorlevel 1 ( pause & exit /b 1 )
pause
