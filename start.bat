@echo off
rem ONCHAIN RADAR always-on runner. start_hidden.vbs calls this without a window.
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\pythonw.exe" main.py run
