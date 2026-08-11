@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\app-node\release\Prepare-AiRelease.ps1" %*
exit /b %ERRORLEVEL%
