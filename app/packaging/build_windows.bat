@echo off
rem Legacy entry point: keep one authoritative Windows/NSIS build pipeline.
call "%~dp0..\..\packaging\build_windows.bat" %*
exit /b %ERRORLEVEL%
