@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-workbuddy.ps1" %*
set "CREATOR_NOTES_EXIT=%ERRORLEVEL%"
echo.
if not "%CREATOR_NOTES_EXIT%"=="0" echo Installation stopped. The previous configuration was not replaced without ownership checks.
pause
exit /b %CREATOR_NOTES_EXIT%
