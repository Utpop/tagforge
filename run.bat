@echo off
rem TagForge desktop launcher (ASCII only: cmd.exe may mis-parse UTF-8 comments)
rem Usage: run.bat [folder] [--scale 1.25]
cd /d "%~dp0"

set "PYEXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\gui\Scripts\python.exe" set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\envs\gui\Scripts\python.exe"
if not defined PYEXE set "PYEXE=python"

"%PYEXE%" -c "import tkinter, mutagen" 2>nul
if errorlevel 1 (
  echo.
  echo [ERROR] Need Python 3.10+ with tkinter, plus mutagen.
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install mutagen
  echo.
  pause
  exit /b 1
)

"%PYEXE%" -m tagforge.gui %*
if errorlevel 1 pause
