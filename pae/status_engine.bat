@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:4815/v1/health' -UseBasicParsing -TimeoutSec 3 | Out-Null; Write-Host '[PAE] engine UP' } catch { Write-Host '[PAE] engine DOWN' }"
pause
