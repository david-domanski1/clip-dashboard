@echo off
REM Starts Clip Dashboard and opens it in your browser. Keep this window open while you use the app;
REM scheduled posts only fire while it runs. Close the window to stop.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Not installed yet. Double-click install.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m clipdash
pause
