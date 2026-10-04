@echo off
rem Report whether the engine is up AND is using this folder's pae.db.
rem The earlier version only checked that /v1/health answered, so any HTTP server
rem on 4815 (or an engine pointed at another database) looked healthy.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $mine = (Join-Path (Get-Location).Path 'pae.db'); try { $h = Invoke-RestMethod 'http://127.0.0.1:4815/v1/health' -TimeoutSec 5; $null = Invoke-RestMethod 'http://127.0.0.1:4815/v1/status' -TimeoutSec 10; if ($h.db -and ($h.db.ToString().ToLower() -eq $mine.ToLower())) { Write-Host ('[PAE] engine UP  (db=' + $h.db + ')') } else { Write-Host '[PAE] NOT OK: something answers on 4815 but it is not using this folder''s pae.db'; Write-Host ('        it reports db=' + $h.db); exit 1 } } catch { Write-Host '[PAE] engine DOWN'; exit 1 }"
if errorlevel 1 ( echo. & pause & exit /b 1 )
pause
