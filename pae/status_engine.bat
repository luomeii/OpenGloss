@echo off
rem Check authenticated health and exact configured DB identity, without secrets.
rem Python honors PAE_CONFIG_PATH, PAE_DB_PATH and PAE_ENGINE_PORT.
rem The helper disables proxies/redirects and decodes health as UTF-8.
rem No source helper or Python availability check starts/stops an engine here.
cd /d "%~dp0"
set "PAE_PY="
python --version >nul 2>nul
if "%errorlevel%"=="0" set "PAE_PY=python"
if defined PAE_PY goto :pae_run
py --version >nul 2>nul
if "%errorlevel%"=="0" set "PAE_PY=py"
if defined PAE_PY goto :pae_run
echo [PAE] ERROR: Python is unavailable.
pause
exit /b 1
:pae_run
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $txt= & %PAE_PY% -B source_health.py; $h=ConvertFrom-Json -InputObject ($txt -join ''); if ($h.error) { Write-Host ('[PAE] health check failed: '+$h.error); exit 1 }; if ($h.ok -and $h.instance_id -and ($h.instance_id.ToString().ToLowerInvariant() -eq $h.expected_instance_id.ToString().ToLowerInvariant())) { Write-Host ('[PAE] engine UP (db='+$h.instance_id+')'); exit 0 }; Write-Host '[PAE] ERROR: health identity does not match this configured instance'; exit 1 } catch { Write-Host '[PAE] health probe failed'; exit 1 }"
if errorlevel 1 ( pause & exit /b 1 )
pause
exit /b 0
