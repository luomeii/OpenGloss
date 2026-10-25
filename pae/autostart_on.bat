@echo off
rem Register a per-user autostart entry for THIS folder.
rem
rem Why it calls start_engine.bat instead of python directly (2026-10-25):
rem the previous version wrote 'python -m uvicorn ...' into the .vbs, so the
rem logon launch depended on PATH resolving 'python' to the bundled interpreter
rem and produced no log at all when it failed.
rem start_engine.bat always uses the bundled python.exe, bypasses any proxy for
rem the health check, and writes engine-start.log / engine-start.err.log.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

if not exist "%~dp0python.exe" (
  echo [PAE] ERROR: python.exe is missing next to this file.
  echo        Re-extract the complete ZIP and run Install.cmd again.
  pause
  exit /b 1
)
if not exist "%~dp0start_engine.bat" (
  echo [PAE] ERROR: start_engine.bat is missing next to this file.
  pause
  exit /b 1
)

set "PAE_VBS=%~dp0autostart_hidden.vbs"

rem The launcher runs start_engine.bat, which does its own logging. Deriving the
rem folder from ScriptFullName avoids the trailing-backslash quote trap that a
rem literal %~dp0 would create inside a VBS string.
> "%PAE_VBS%" echo Set fso = CreateObject("Scripting.FileSystemObject")
>> "%PAE_VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%PAE_VBS%" echo sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
>> "%PAE_VBS%" echo sh.Run "%~dp0start_engine.bat", 0, False
if not exist "%PAE_VBS%" ( echo [PAE] ERROR: could not write the launcher. & pause & exit /b 1 )
echo [PAE] launcher written: %PAE_VBS%
echo [PAE] launcher content:
type "%PAE_VBS%"
echo.

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $v='wscript.exe ' + $q + '%PAE_VBS%' + $q; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'OpenGloss' -Value $v -Force"
if "%errorlevel%"=="0" goto :pae_ok
echo [PAE] ERROR: could not write the Run key.
pause
exit /b 1

:pae_ok
echo [PAE] Autostart is ON. The engine will start (hidden) at every logon.
echo       Start log: %~dp0engine-start.log
echo       Turn it off again with autostart_off.bat.
pause
exit /b 0
