@echo off
rem One-click start. The engine is launched HIDDEN and keeps running after this
rem window closes, so you get the visual equivalent of an app launcher.
rem
rem It does not re-implement anything: it calls the already-tested
rem start_engine.bat (which verifies the engine really came up on THIS folder's
rem pae.db) and feeds it EOF so its trailing 'pause' returns at once.
rem The window only stays open when something actually failed.
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"
if not exist "%~dp0start_engine.bat" ( echo [PAE] ERROR: start_engine.bat is missing next to this file. & pause & exit /b 1 )
call "%~dp0start_engine.bat" < nul
if errorlevel 1 ( echo. & echo [PAE] The engine did NOT start - read the message above. & pause & exit /b 1 )
echo.
echo [PAE] Engine is running hidden in the background.
echo       This window closes in 3 seconds. Run quick_stop.bat to stop the engine.
timeout /t 3 >nul
exit /b 0
