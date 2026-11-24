@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Install-Sights.ps1" -Mode NoReload
echo.
pause
