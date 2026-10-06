@echo off
rem Make the engine start automatically when THIS user logs in.
rem
rem How it works: this writes a tiny hidden launcher (autostart_hidden.vbs) into
rem this folder and points the per-user Run key at it. WScript's window style 0
rem means the engine comes up with no console window at all - not even a flash.
rem Per-user (HKCU), so it needs no administrator rights.
rem
rem NOTE: because the launcher is registered per-user and stores an absolute
rem path, moving or renaming this folder later requires running this script
rem again. autostart_off.bat undoes everything this script does.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

rem ---- why this guard looks odd ----------------------------------------
rem 1) 'where python' is not a usable test: the Microsoft Store stub is a
rem    real python.exe that 'where' finds but which cannot run anything.
rem 2) the exit code is compared as a STRING, never with 'if errorlevel 1':
rem    a broken install exits with a NEGATIVE code, which 'if errorlevel 1'
rem    does not catch and would report success.
rem 3) no pipe and no nested 'exit /b': a failed pipeline aborts the script
rem    with a bare 255, and an 'exit /b N' that is not the LAST statement of
rem    its parenthesised block loses N (cmd parses the whole block first).
set "PAE_PY="
python --version >nul 2>nul
if "%errorlevel%"=="0" set "PAE_PY=python"
if defined PAE_PY goto :pae_run
py --version >nul 2>nul
if "%errorlevel%"=="0" set "PAE_PY=py"
if defined PAE_PY goto :pae_run
echo [PAE] ERROR: python is not usable in this terminal.
echo        Install Python 3.10+ from python.org and CHECK "Add python.exe to PATH",
echo        then reopen this window and retry.
pause
exit /b 1

:pae_run

set "PAE_VBS=%~dp0autostart_hidden.vbs"
rem --- write the hidden launcher (python is resolved from PATH at logon) ---
> "%PAE_VBS%" echo Set fso = CreateObject("Scripting.FileSystemObject")
>> "%PAE_VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%PAE_VBS%" echo sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
>> "%PAE_VBS%" echo sh.Run "python -m uvicorn pae_core.api:app --port 4815", 0, False
if not exist "%PAE_VBS%" ( echo [PAE] ERROR: could not write the launcher. & pause & exit /b 1 )
echo [PAE] launcher written: %PAE_VBS%

rem --- register it for this user (quotes are built in PowerShell so that this
rem     file never contains a backslash-escaped quote: cmd counts quotes to
rem     decide whether a bar is a pipe, and a stray escaped quote breaks that) ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $v='wscript.exe ' + $q + '%~dp0autostart_hidden.vbs' + $q; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'OpenGloss' -Value $v; Write-Host ('[PAE] registered for this user: ' + $v)"
if "%errorlevel%"=="0" goto :pae_ok
echo [PAE] ERROR: could not write the Run key.
pause
exit /b 1

:pae_ok
echo [PAE] Autostart is ON. The engine will start (hidden) at every logon.
echo       Turn it off again with autostart_off.bat.
pause
exit /b 0
