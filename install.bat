@echo off
REM Clip Dashboard installer for Windows. Double-click this file.
REM Creates a private Python environment in .venv and installs everything, including ffmpeg (via the imageio-ffmpeg package).
cd /d "%~dp0"
echo == Clip Dashboard installer (Windows) ==

set PY=
for %%c in (py python3 python) do (
  if not defined PY (
    %%c -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set PY=%%c
  )
)
if not defined PY (
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH", then run this installer again.
  pause
  exit /b 1
)
%PY% --version

if not exist .venv\Scripts\python.exe (
  echo Creating private environment ^(.venv^)...
  %PY% -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
)
echo Installing packages (a few minutes the first time)...
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt -q || (echo Package install failed. Check your internet connection and run again. & pause & exit /b 1)
.venv\Scripts\python.exe -c "import imageio_ffmpeg; print('ffmpeg:', imageio_ffmpeg.get_ffmpeg_exe())"
echo.
echo Installed. Double-click Launch.bat to start Clip Dashboard.
pause
