@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Resolve directories relative to this file
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
set "UI_DIR=%ROOT_DIR%\ui"
set "COMPOSE_DIR=%ROOT_DIR%\compose"

rem Prefer .venv-04, fall back to .venv
set "VENV_DIR=%UI_DIR%\.venv-04"
if not exist "%VENV_DIR%\Scripts\python.exe" (
  if exist "%UI_DIR%\.venv\Scripts\python.exe" (
    set "VENV_DIR=%UI_DIR%\.venv"
  )
)

rem Create venv if missing
if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Creating virtualenv at "%VENV_DIR%" …
  set "PY_EXE="
  for %%P in (py.exe) do if exist "%%~$PATH:P" set "PY_EXE=py"
  if not defined PY_EXE (
    for %%P in (python.exe) do if exist "%%~$PATH:P" set "PY_EXE=python"
  )
  if not defined PY_EXE (
    echo Error: Could not find Python. Please install Python 3.10+ and try again.
    pause
    exit /b 1
  )
  if "%PY_EXE%"=="py" (
    py -3.11 -m venv "%VENV_DIR%" || py -3 -m venv "%VENV_DIR%"
  ) else (
    python -m venv "%VENV_DIR%"
  )
  "%VENV_DIR%\Scripts\python.exe" -m pip install -U pip
  "%VENV_DIR%\Scripts\python.exe" -m pip install -r "%UI_DIR%\requirements.txt"
)

rem Point UI to bundled compose project
set "COYOTE_COMPOSE_DIR=%COMPOSE_DIR%"

rem Try to open browser after a short delay
start "" cmd /c "timeout /t 2 /nobreak >nul && start "" http://localhost:8080"

echo Starting Coyote UI…
cd /d "%UI_DIR%"
"%VENV_DIR%\Scripts\python.exe" "coyote_ui_server.py"
