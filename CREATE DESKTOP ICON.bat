@echo off
title Market Sniper - create desktop icon
cd /d "%~dp0"

echo.
echo   Creating a pinnable MARKET SNIPER icon on your Desktop...
echo.

rem  WHY THIS LOOKS INDIRECT
rem
rem  1. The shortcut cannot point at the real launcher. Its name starts with an
rem     emoji, and WScript.Shell rejects any TargetPath holding a character
rem     outside the Basic Multilingual Plane - "Value does not fall within the
rem     expected range" - and leaves a dead icon behind. So it points at
rem     MARKET SNIPER.bat, which is plain ASCII and hands over to the launcher.
rem
rem  2. Windows refuses to PIN a shortcut whose target is a .bat file. That is
rem     a shell rule, not a setting, and no amount of right-clicking changes
rem     it. So the target is cmd.exe - a real executable, which Windows is
rem     happy to pin - and the batch file is passed as an argument.

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
  "$bat = Join-Path $dir 'MARKET SNIPER.bat';" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$link = Join-Path $ws.SpecialFolders('Desktop') 'MARKET SNIPER.lnk';" ^
  "if (Test-Path $link) { Remove-Item $link -Force };" ^
  "$s = $ws.CreateShortcut($link);" ^
  "$s.TargetPath = (Join-Path $env:SystemRoot 'System32\cmd.exe');" ^
  "$s.Arguments = '/c \"' + $bat + '\"';" ^
  "$s.WorkingDirectory = $dir;" ^
  "$ico = Join-Path $dir 'sniper.ico';" ^
  "if (Test-Path $ico) { $s.IconLocation = \"$ico,0\" };" ^
  "$s.Description = 'Start Market Sniper and open the trading screen';" ^
  "$s.Save();" ^
  "$c = $ws.CreateShortcut($link);" ^
  "if (-not (Test-Path $c.TargetPath)) { throw 'shortcut saved but the target is missing' };" ^
  "if (-not (Test-Path $bat)) { throw 'shortcut saved but MARKET SNIPER.bat is missing' };" ^
  "Write-Host '';" ^
  "Write-Host ('   Created  : ' + $link);" ^
  "Write-Host ('   Runs     : ' + $c.TargetPath + ' ' + $c.Arguments);" ^
  "Write-Host '   Verified : target and batch file both exist.'"

if errorlevel 1 (
  echo.
  echo   ------------------------------------------------------------
  echo   Could not create the shortcut automatically.
  echo.
  echo   By hand:
  echo     1. right-click your Desktop, New ^> Shortcut
  echo     2. paste this as the location, quotes included:
  echo          cmd.exe /c "%~dp0MARKET SNIPER.bat"
  echo     3. name it MARKET SNIPER
  echo     4. right-click it, Properties, Change Icon,
  echo        and pick sniper.ico from this folder
  echo   ------------------------------------------------------------
  echo.
  pause
  exit /b 1
)

echo.
echo   ============================================================
echo     NOW PIN IT:
echo       right-click the Desktop icon  ^>  Pin to taskbar
echo.
echo     That works now because the shortcut runs cmd.exe rather
echo     than pointing straight at a .bat file, which Windows
echo     refuses to pin no matter how you ask.
echo.
echo     If "Pin to taskbar" is not in the menu, hold SHIFT and
echo     right-click the icon - it is on the extended menu on
echo     some builds of Windows 11.
echo   ============================================================
echo.
echo   You only need to run this once.
echo.
pause
