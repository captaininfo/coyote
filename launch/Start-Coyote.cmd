@echo off
setlocal
cd /d "%~dp0..\ui"
if not exist .venv\Scripts\python.exe (
  py -3.11 -m venv .venv
  .venv\Scripts\python -m pip install -U pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
set COYOTE_COMPOSE_DIR=%~dp0..\compose
.venv\Scripts\python coyote_ui_server.py
