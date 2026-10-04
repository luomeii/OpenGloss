@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $p = Get-NetTCPConnection -LocalPort 4815 -State Listen; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; Write-Host '[PAE] engine stopped' } else { Write-Host '[PAE] not running' }"
pause
