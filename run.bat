@echo off
setlocal
set "DIR=%~dp0"
set "PYTHONPATH=%DIR%src;%PYTHONPATH%"

if exist "%DIR%venv\Scripts\python.exe" (
    "%DIR%venv\Scripts\python.exe" -m hifi_tui %*
) else (
    python -m hifi_tui %*
)
