@echo off
rem OpenGloss learning-data backup.
rem Uses SQLite VACUUM INTO to produce a self-consistent single-file snapshot.
rem Do NOT replace this with "copy pae.db": in WAL mode that can silently lose
rem the newest writes, or even produce an EMPTY database that still passes
rem integrity_check. See backup.py for the full explanation.
cd /d "%~dp0"
python "%~dp0backup.py"
if errorlevel 1 py "%~dp0backup.py"
pause
