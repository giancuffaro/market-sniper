@echo off
setlocal EnableDelayedExpansion
title Market Sniper - install NinjaTrader files
cd /d "%~dp0"

echo.
echo   ============================================================
echo     INSTALLING THE MARKET SNIPER FILES INTO NINJATRADER
echo   ============================================================
echo.

rem  Documents is NOT always %USERPROFILE%\Documents. OneDrive redirects it,
rem  and so does any corporate folder-redirection policy. Asking Windows for
rem  the real path is the only way that survives both.
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('MyDocuments')"`) do set "DOCS=%%D"

if not defined DOCS (
  echo   ERROR: could not work out where your Documents folder is.
  echo.
  pause
  exit /b 1
)

set "NT=%DOCS%\NinjaTrader 8"
set "NTIND=%NT%\bin\Custom\Indicators"
set "NTSTR=%NT%\bin\Custom\Strategies"

echo   Documents      : %DOCS%
echo   NinjaTrader 8  : %NT%
echo.

if not exist "%NT%" (
  echo   ############################################################
  echo   #  NinjaTrader 8 is not installed under that Documents
  echo   #  folder, so there is nowhere to put these files.
  echo   #
  echo   #  Install / open NinjaTrader 8 once, then run this again.
  echo   ############################################################
  echo.
  pause
  exit /b 1
)

rem  NinjaTrader recompiles everything in bin\Custom when it STARTS. Copying
rem  while it is running means the files sit there unread until you compile by
rem  hand - which is the step that kept going wrong. Closing it first turns
rem  "copy, then find the right menu" into "copy, then just open NinjaTrader".
tasklist /fi "imagename eq NinjaTrader.exe" 2>nul | find /i "NinjaTrader.exe" >nul
if not errorlevel 1 (
  echo   ############################################################
  echo   #  NINJATRADER IS RUNNING.
  echo   #
  echo   #  Close it completely, then run this file again.
  echo   #  It compiles these files when it starts, so installing
  echo   #  while it is closed means there is nothing left to do
  echo   #  by hand afterwards.
  echo   ############################################################
  echo.
  pause
  exit /b 1
)

if not exist "%NTIND%" mkdir "%NTIND%" >nul 2>&1
if not exist "%NTSTR%" mkdir "%NTSTR%" >nul 2>&1

set "SRC=%~dp0ninjatrader"
set FAILED=0

call :install "MarketSniperTrend.cs"   "%NTIND%" "indicator"
call :install "MarketSniperRatchet.cs" "%NTSTR%" "strategy"

echo.
if "%FAILED%"=="1" (
  echo   ############################################################
  echo   #  One or more files did not install. Nothing was compiled.
  echo   #  Send the messages above to Claude.
  echo   ############################################################
  echo.
  pause
  exit /b 1
)

echo   ============================================================
echo     DONE. Both files are in place.
echo.
echo     NOW OPEN NINJATRADER 8. It compiles them on startup -
echo     there is nothing else for you to click.
echo.
echo     Then they appear like anything built in:
echo       chart right-click ^> Indicators ^> MarketSniperTrend
echo       Control Center ^> Strategies ^> MarketSniperRatchet
echo.
echo     If NinjaTrader shows a compile error box on startup,
echo     screenshot it and send it over.
echo   ============================================================
echo.
pause
exit /b 0


:install
rem  %1 = filename   %2 = destination folder   %3 = what it is
set "FN=%~1"
set "DEST=%~2"
set "KIND=%~3"

if not exist "%SRC%\%FN%" (
  echo   [ %KIND% ] MISSING: %SRC%\%FN%
  echo              The file is not in the ninjatrader folder. If you
  echo              DRAGGED it somewhere earlier, Windows MOVED it -
  echo              a drag within the same drive is a move, not a copy.
  set FAILED=1
  goto :eof
)

rem  COPY, never move. This file must be able to run twice without emptying
rem  the folder it reads from.
copy /y "%SRC%\%FN%" "%DEST%\%FN%" >nul 2>&1

if not exist "%DEST%\%FN%" (
  echo   [ %KIND% ] FAILED to copy into:
  echo              %DEST%
  echo              Is NinjaTrader really closed? A file it has open
  echo              cannot be overwritten.
  set FAILED=1
  goto :eof
)

rem  Verify it actually landed and is not a truncated copy. "It exists" is not
rem  the same as "it is the file" - a half-written copy compiles to nonsense.
for %%A in ("%SRC%\%FN%")  do set SZ1=%%~zA
for %%A in ("%DEST%\%FN%") do set SZ2=%%~zA
if not "!SZ1!"=="!SZ2!" (
  echo   [ %KIND% ] COPIED BUT WRONG SIZE: !SZ2! bytes, expected !SZ1!
  set FAILED=1
  goto :eof
)

echo   [ %KIND% ] installed: %FN%  ^(!SZ2! bytes^)
echo              -^> %DEST%
goto :eof
