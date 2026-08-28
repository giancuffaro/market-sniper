@echo off
rem  ASCII-named launcher.
rem
rem  This exists for one reason: a Windows shortcut cannot point at the real
rem  launcher. Its filename begins with an emoji, and WScript.Shell throws
rem  "Value does not fall within the expected range" the moment you assign a
rem  TargetPath containing a character outside the Basic Multilingual Plane.
rem
rem  So the Desktop icon points HERE, and this hands straight over to the real
rem  launcher - found by wildcard, so the emoji is never written in this file
rem  either (a .bat is read in the OEM codepage, where it would not survive).

cd /d "%~dp0"

set "LAUNCHER="
for %%F in ("*START MARKET SNIPER.bat") do set "LAUNCHER=%%F"

if not defined LAUNCHER (
  echo.
  echo   Could not find the Market Sniper launcher in:
  echo     %~dp0
  echo.
  echo   Expected a file whose name ends with:  START MARKET SNIPER.bat
  echo.
  pause
  exit /b 1
)

rem  CALL, so everything stays in THIS window and this process tree - closing
rem  the window still takes both servers down, exactly as before.
call "%LAUNCHER%"
