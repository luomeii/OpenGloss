@echo off
rem One-click stop. Calls the already-tested stop_engine.bat, which checks that
rem the process on the port really IS our engine before killing it (so an
rem unrelated program that happens to use the port is left alone).
rem Keep this file ASCII-only: cmd.exe reads .bat in the OEM code page.
cd /d "%~dp0"
if not exist "%~dp0stop_engine.bat" ( echo [PAE] ERROR: stop_engine.bat is missing next to this file. & pause & exit /b 1 )
call "%~dp0stop_engine.bat" < nul
if errorlevel 1 ( echo. & echo [PAE] Could not stop the engine - read the message above. & pause & exit /b 1 )
echo.
echo [PAE] Done. The engine is not running now.
timeout /t 3 >nul
exit /b 0
