@echo off
rem Undo everything autostart_on.bat did: remove the per-user Run entry and
rem delete the generated launcher. Safe to run even if autostart was never set.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$k='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'; $v=(Get-ItemProperty -Path $k -Name 'OpenGloss' -ErrorAction SilentlyContinue).OpenGloss; if ($v) { Remove-ItemProperty -Path $k -Name 'OpenGloss' -ErrorAction SilentlyContinue; Write-Host ('[PAE] removed the Run entry: ' + $v) } else { Write-Host '[PAE] no Run entry was present.' }"
if not "%errorlevel%"=="0" ( echo [PAE] ERROR: could not edit the Run key. & pause & exit /b 1 )

if exist "%~dp0autostart_hidden.vbs" ( del /f /q "%~dp0autostart_hidden.vbs" & echo [PAE] launcher deleted. )
if exist "%~dp0autostart_hidden.vbs" ( echo [PAE] ERROR: the launcher file is still there. & pause & exit /b 1 )

echo [PAE] Autostart is OFF.
pause
exit /b 0
