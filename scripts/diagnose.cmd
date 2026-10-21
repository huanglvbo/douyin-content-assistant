@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose.ps1" %*
set "CREATOR_NOTES_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %CREATOR_NOTES_EXIT%
