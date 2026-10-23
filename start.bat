@echo off
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
  echo or download CalexLights.exe from the GitHub Releases page instead.
  pause & exit /b 1
)
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if not exist devices.json (
  python -m tinytuya wizard
  if not exist devices.json pause & exit /b 1
)
rem pythonw runs without a console window; the app lives in the system tray.
start "" pythonw server.py %*
