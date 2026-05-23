@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fanqie Web Downloader

echo.
echo =======================================
echo   Fanqie Novel Web Downloader
echo =======================================
echo.

set "ROOT=%~dp0"
set "PYEXE="

set "PATH=C:\python;C:\python\Scripts;%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python313\Scripts;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%LOCALAPPDATA%\Programs\Python\Python310;%LOCALAPPDATA%\Programs\Python\Python310\Scripts;%PATH%"

if exist "C:\python\python.exe" set "PYEXE=C:\python\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE if exist "%ProgramFiles%\Python312\python.exe" set "PYEXE=%ProgramFiles%\Python312\python.exe"

if not defined PYEXE (
    echo [ERROR] Python not found.
    echo Your Python may not be in PATH when launching .bat by double-click.
    echo Try: reinstall Python and check "Add python.exe to PATH"
    echo Or in terminal: cd src ^&^& C:\python\python.exe server.py
    echo.
    pause
    exit /b 1
)

echo Using Python: %PYEXE%

set "PY=%ROOT%venv\Scripts\python.exe"
set "PIP=%ROOT%venv\Scripts\pip.exe"

if not exist "%PY%" (
    echo [1/4] Creating venv...
    "%PYEXE%" -m venv "%ROOT%venv"
    if errorlevel 1 (
        echo [ERROR] venv failed.
        pause
        exit /b 1
    )
)

if not exist "%ROOT%venv\.deps_ok" (
    echo [2/4] Installing dependencies...
    "%PIP%" install -r "%ROOT%requirements.txt"
    if errorlevel 1 (
        echo [ERROR] pip install failed.
        pause
        exit /b 1
    )
    echo [3/4] Installing Playwright...
    "%PY%" -m playwright install chromium
    echo ok>"%ROOT%venv\.deps_ok"
) else (
    echo Dependencies OK.
)

echo [4/4] Starting server, opening browser...
echo Close this window to stop.
echo.
set FANQIE_OPEN_BROWSER=1
cd /d "%ROOT%src"
"%PY%" server.py
echo.
echo Server stopped.
pause
endlocal
