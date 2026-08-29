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

rem  NinjaTrader must be CLOSED: it holds these files open and the copy fails
rem  otherwise. It does NOT reliably recompile at startup - that was tested and
rem  the compile timestamp did not move - so a manual Compile in the NinjaScript
rem  Editor is still needed afterwards. This file does the copying, not that.
tasklist /fi "imagename eq NinjaTrader.exe" 2>nul | find /i "NinjaTrader.exe" >nul
if not errorlevel 1 (
  echo   ############################################################
  echo   #  NINJATRADER IS RUNNING.
  echo   #
  echo   #  Close it completely, then run this file again.
  echo   #  A file it has open cannot be overwritten, so it has to
  echo   #  be closed while these are copied in.
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
echo     NOW OPEN NINJATRADER 8, then compile once:
echo       New menu  ^>  NinjaScript Editor
echo       right-click inside the editor  ^>  Compile
echo.
echo     ^(NinjaTrader does not reliably compile on startup, so this
echo      one click is needed. It is the only one.^)
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
