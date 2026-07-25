@echo off
REM Double-click this to run the tracker. Keeps the window open afterwards.

cd /d "%~dp0"

REM UTF-8: without this, cmd's legacy codepage turns the table's box lines and
REM the "..." truncation marker into garbage characters.
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8

REM Widen the console so all eight columns fit (the layout drops columns at 80).
mode con: cols=120 lines=40 >nul 2>&1

python main.py menu
if errorlevel 9009 (
    echo.
    echo   Python was not found on your PATH.
    echo   Install it from https://python.org and tick "Add python.exe to PATH".
)

echo.
pause
