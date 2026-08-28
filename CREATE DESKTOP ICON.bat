@echo off
title Market Sniper - create desktop icon
cd /d "%~dp0"

echo.
echo   Creating a MARKET SNIPER icon on your Desktop...
echo.

rem  The shortcut points at "MARKET SNIPER.bat", NOT at the real launcher.
rem  The real launcher's name starts with an emoji, and WScript.Shell refuses
rem  any TargetPath holding a character outside the Basic Multilingual Plane -
rem  it fails with "Value does not fall within the expected range" and leaves
rem  you an icon that does nothing. MARKET SNIPER.bat is plain ASCII and hands
rem  straight over to the real launcher.

if not exist "%~dp0MARKET SNIPER.bat" (
  echo   ERROR: "MARKET SNIPER.bat" is missing from this folder.
  echo   It is required - the Desktop icon points at it.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$dir = '%~dp0'.TrimEnd('\');" ^
  "$target = Join-Path $dir 'MARKET SNIPER.bat';" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$link = Join-Path $ws.SpecialFolders('Desktop') 'MARKET SNIPER.lnk';" ^
  "if (Test-Path $link) { Remove-Item $link -Force };" ^
  "$s = $ws.CreateShortcut($link);" ^
  "$s.TargetPath = $target;" ^
  "$s.WorkingDirectory = $dir;" ^
  "$ico = Join-Path $dir 'sniper.ico';" ^
  "if (Test-Path $ico) { $s.IconLocation = \"$ico,0\" };" ^
  "$s.Description = 'Start Market Sniper and open the trading screen';" ^
  "$s.Save();" ^
  "$check = $ws.CreateShortcut($link);" ^
  "if (-not (Test-Path $check.TargetPath)) { throw 'the shortcut saved but points nowhere' };" ^
  "Write-Host '';" ^
  "Write-Host ('   Created : ' + $link);" ^
  "Write-Host ('   Runs    : ' + $check.TargetPath);" ^
  "Write-Host '   Verified: the target exists.'"

if errorlevel 1 (
  echo.
  echo   ------------------------------------------------------------
  echo   Could not create the shortcut automatically.
  echo.
  echo   Do it by hand instead - it takes five seconds:
  echo     1. right-click "MARKET SNIPER.bat" in this folder
  echo     2. Send to  ^>  Desktop ^(create shortcut^)
  echo     3. right-click the new Desktop icon, Properties,
  echo        Change Icon, and pick "sniper.ico" from this folder
  echo   ------------------------------------------------------------
  echo.
  pause
  exit /b 1
)

echo.
echo   ============================================================
echo     Double-click the green crosshair on your Desktop and it:
echo       1. starts both apps
echo       2. opens the trading screen in your browser
echo       3. hides itself to the system tray
echo.
echo     TO PIN IT TO THE TASKBAR:
echo       drag the Desktop icon onto your taskbar.
echo       ^(Windows blocks "Pin to taskbar" for shortcuts that
echo        point at .bat files. Dragging works.^)
echo   ============================================================
echo.
echo   You only need to run this once.
echo.
pause
