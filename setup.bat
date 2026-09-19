@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
 echo Python 3.11 or newer is required. Install it and enable Add to PATH.
 exit /b 1
)
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\update_zipcodes.py
if errorlevel 1 (
 echo Nationwide ZIP data installation failed. See README.md. The offline demo still works.
 exit /b 1
)
if not exist .env copy .env.example .env >nul
set HOST=127.0.0.1
echo Open http://localhost:8000 in your browser. Press Ctrl+C to stop.
.venv\Scripts\python.exe scripts\serve.py
