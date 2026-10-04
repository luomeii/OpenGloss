@echo off
rem OpenGloss learning-data backup -- see backup.py for why VACUUM INTO is used
rem instead of copying pae.db (a plain copy loses the newest writes, and can even
rem produce an empty database that still passes integrity_check).
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

rem Why this guard looks odd -- three cmd.exe traps:
rem  1) 'where python' is not usable: the Microsoft Store stub is a real
rem     python.exe that 'where' finds but which cannot run anything.
rem  2) exit codes are compared as STRINGS ("%errorlevel%"=="0"), never with
rem     'if errorlevel 1': a broken install (missing DLL) or a crashed child
rem     exits in the 0x8xxxxxxx range, i.e. NEGATIVE, and 'if errorlevel 1'
rem     does not catch those -- it would report success.
rem  3) no pipe and no nested 'exit /b': a failed pipeline aborted the script
rem     with a bare 255, and an 'exit /b N' that is not the last statement of
rem     its parenthesised block loses N (cmd parses the block first).
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

%PAE_PY% "%~dp0backup.py"
rem Compare as a string: backup.py dying from a missing DLL or a crash exits
rem NEGATIVE, and 'if errorlevel 1' would let that through as success.
if not "%errorlevel%"=="0" goto :pae_failed
pause
exit /b 0

:pae_failed
echo [PAE] ERROR: backup failed - see the message above.
pause
exit /b 1
