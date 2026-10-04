@echo off
rem Report whether the engine is up AND is using this folder's pae.db.
rem
rem NOTE: PowerShell 5.1's Invoke-RestMethod decodes a charset-less
rem application/json body as ISO-8859-1, so a db path containing non-ASCII
rem characters comes back as mojibake and every comparison fails. That is why
rem this script reads the RAW bytes and decodes them as UTF-8 itself.
rem The previous version only checked that /v1/health answered at all, so any
rem HTTP server on 4815 (or an engine using another database) looked healthy.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $mine = (Join-Path (Get-Location).Path 'pae.db'); try { $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:4815/v1/health' -UseBasicParsing -TimeoutSec 5; $txt = [System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray()); $h = $txt | ConvertFrom-Json; $null = Invoke-WebRequest -Uri 'http://127.0.0.1:4815/v1/status' -UseBasicParsing -TimeoutSec 15; if ($h.db -and ($h.db.ToString().ToLower() -eq $mine.ToLower())) { Write-Host ('[PAE] engine UP  (db=' + $h.db + ')') } else { Write-Host '[PAE] NOT OK: something answers on 4815 but it is not using this folder''s pae.db'; Write-Host ('        it reports db=' + $h.db); exit 1 } } catch { Write-Host '[PAE] engine DOWN'; exit 1 }"
if errorlevel 1 ( echo. & pause & exit /b 1 )
pause
