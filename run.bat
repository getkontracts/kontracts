@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Run START-WINDOWS.bat first.
 exit /b 1
)
set HOST=127.0.0.1
echo Open http://localhost:8000. Press Ctrl+C to stop.
.venv\Scripts\python.exe scripts\serve.py
