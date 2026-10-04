@echo off
rem Report whether the engine is up AND is using this folder's pae.db.
rem
rem NOTE 1: PowerShell 5.1's Invoke-RestMethod decodes a charset-less
rem application/json body as ISO-8859-1, so a db path containing non-ASCII
rem characters comes back as mojibake and every comparison fails. We read the
rem RAW bytes and decode them as UTF-8 ourselves.
rem NOTE 2: /v1/health really touches the engine, and a cold engine spends
rem ~25s loading the 65 MB dictionary, so the timeout below must exceed that.
rem NOTE 3: a 503 is NOT 'down' -- it means the engine is running but could
rem not build (missing or corrupt dictionary). Its body carries the reason.
rem
rem NOTE 4 - IMPORTANT, and it applies to the powershell -Command line below:
rem that line must contain an EVEN number of double quotes (in practice just
rem the one pair around the script) and no pipe inside the argument.
rem cmd.exe counts quote characters to decide whether a bar is a pipe, and it
rem does not treat a backslash as an escape. So a backslash-escaped quote
rem inside the command flips the parity, cmd splits the line at the next bar,
rem and the tail runs as a separate command -- symptom: ConvertFrom-Json is
rem not recognized, exit code 255, no output.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; $mine = (Join-Path (Get-Location).Path 'pae.db'); $resp = $null; try { $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:4815/v1/health' -UseBasicParsing -TimeoutSec 60 } catch {   $code = 0; $body = '';   try { $code = [int]$_.Exception.Response.StatusCode } catch { };   try { $body = $_.ErrorDetails.Message } catch { }; try { $body = [System.Text.Encoding]::UTF8.GetString([System.Text.Encoding]::GetEncoding(28591).GetBytes($body)) } catch { };   if ($code -eq 503) {     Write-Host '[PAE] engine is RUNNING but NOT healthy (it answered 503):';     Write-Host ('        ' + $body)   } else {     Write-Host '[PAE] engine DOWN (nothing is listening on 4815)'   }   exit 1 }; $txt = [System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray()); $h = ConvertFrom-Json -InputObject $txt; if ($h.db -and ($h.db.ToString().ToLower() -eq $mine.ToLower())) {   Write-Host ('[PAE] engine UP  (db=' + $h.db + ')') } else {   Write-Host '[PAE] NOT OK: something answers on 4815 but it is not using the pae.db of this folder';   Write-Host ('        it reports db=' + $h.db);   exit 1 }"
if errorlevel 1 ( echo. & pause & exit /b 1 )
pause
exit /b 0
