@echo off
rem Build the Windows app: dist\Clip Dashboard\Clip Dashboard.exe and dist\Clip-Dashboard-<version>-win-x64.zip
rem Usage: double-click, or run scripts\build_win.bat from a Command Prompt.
rem Optional signing (see packaging\README.md): set CLIPDASH_SIGN_CERT=<certificate thumbprint> before running.
setlocal
cd /d "%~dp0\.."
set "PY=%PYTHON%"
if "%PY%"=="" set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (echo No Python at %PY%. Run install.bat first or set PYTHON. & exit /b 1)
"%PY%" -c "import PyInstaller" 2>nul || (echo PyInstaller missing: "%PY%" -m pip install -r requirements-dev.txt & exit /b 1)
for /f "delims=" %%v in ('"%PY%" -c "import clipdash; print(clipdash.__version__)"') do set "VERSION=%%v"
echo == Building Clip Dashboard %VERSION% for Windows ==
"%PY%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\clipdash.spec || exit /b 1

if not "%CLIPDASH_SIGN_CERT%"=="" (
  echo == Signing ==
  signtool sign /sha1 %CLIPDASH_SIGN_CERT% /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\Clip Dashboard\Clip Dashboard.exe" || exit /b 1
  signtool verify /pa "dist\Clip Dashboard\Clip Dashboard.exe" || exit /b 1
)

set "ZIP=dist\Clip-Dashboard-%VERSION%-win-x64.zip"
if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Clip Dashboard' -DestinationPath '%ZIP%'" || exit /b 1
echo Done: dist\Clip Dashboard\Clip Dashboard.exe
echo       %ZIP%
endlocal
