@echo off
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
  echo or download CalexLights.exe from the GitHub Releases page instead.
  pause & exit /b 1
)
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
python server.py %*
pause
