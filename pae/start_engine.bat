@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $p = Get-NetTCPConnection -LocalPort 4815 -State Listen; if ($p) { Write-Host ('[PAE] already running, PID ' + $p[0].OwningProcess) } else { Start-Process -WindowStyle Hidden python -ArgumentList '-m','uvicorn','pae_core.api:app','--port','4815' -WorkingDirectory (Get-Location).Path; Write-Host '[PAE] engine starting... first request loads dictionary (~15s)' }"
pause
