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

rem --- Resolve a working Python interpreter (prefer launcher; avoid WindowsApps alias) ---
set "PY_EXE="

for /f "usebackq delims=" %%I in (`where py 2^>NUL`) do set "PY_EXE=py"
if not defined PY_EXE (
  for /f "usebackq delims=" %%I in (`where python 2^>NUL`) do (
    echo %%I | findstr /i "WindowsApps" >NUL || (
      set "PY_EXE=python"
    )
  )
)

if not defined PY_EXE (
  echo Error: No usable Python found.
  echo  - Turn OFF the "python.exe" alias: Settings > Apps > App execution aliases
  echo  - Or install Python 3.10 or newer with "Install launcher" and "Add python.exe to PATH"
  pause
  exit /b 1
)

rem --- Create venv if missing ---
if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Creating virtualenv at "%VENV_DIR%" ...
  if "%PY_EXE%"=="py" (
    py -3.11 -m venv "%VENV_DIR%" || py -3 -m venv "%VENV_DIR%"
  ) else (
    python -m venv "%VENV_DIR%"
  )
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Error: venv was not created at "%VENV_DIR%".
  pause
  exit /b 1
)

"%VENV_DIR%\Scripts\python.exe" -m pip install -U pip
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%UI_DIR%\requirements.txt"


rem Point UI to bundled compose project
set "COYOTE_COMPOSE_DIR=%COMPOSE_DIR%"

rem Try to open browser after a short delay
start "" cmd /c "timeout /t 2 /nobreak >nul && start "" http://localhost:8080"

echo Starting Coyote UI…
cd /d "%UI_DIR%"
"%VENV_DIR%\Scripts\python.exe" "coyote_ui_server.py"