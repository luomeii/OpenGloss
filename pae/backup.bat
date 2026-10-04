@echo off
rem OpenGloss learning-data backup -- see backup.py for why VACUUM INTO is used
rem instead of copying pae.db (a plain copy loses the newest writes, and can even
rem produce an empty database that still passes integrity_check).
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"

rem Try python, then the py launcher -- but run the script only once.
python --version >nul 2>nul
if errorlevel 1 (
  py --version >nul 2>nul
  if errorlevel 1 (
    echo [PAE] ERROR: python is not usable in this terminal.
    echo        Install Python 3.10+ and CHECK "Add python.exe to PATH".
    pause
    exit /b 1
  )
  py "%~dp0backup.py"
) else (
  python "%~dp0backup.py"
)
if errorlevel 1 ( echo [PAE] ERROR: backup failed - see the message above. & pause & exit /b 1 )
pause
