@echo off
title Market Sniper - did the NinjaTrader install work?
cd /d "%~dp0"

echo.
echo   ============================================================
echo     CHECKING YOUR NINJATRADER INSTALL
echo   ============================================================
echo.

rem  Everything below is PowerShell because batch cannot compare file
rem  timestamps without pain, and the whole point of this file is to answer
rem  "did it work" without you having to find anything in the NinjaTrader UI.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue';" ^
  "$docs=[Environment]::GetFolderPath('MyDocuments');" ^
  "$nt=Join-Path $docs 'NinjaTrader 8';" ^
  "if(-not (Test-Path $nt)){ Write-Host '   NinjaTrader 8 is not installed under' $docs -ForegroundColor Red; exit 1 };" ^
  "$ind=Join-Path $nt 'bin\Custom\Indicators\MarketSniperTrend.cs';" ^
  "$str=Join-Path $nt 'bin\Custom\Strategies\MarketSniperRatchet.cs';" ^
  "$dll=Join-Path $nt 'bin\Custom\NinjaTrader.Custom.dll';" ^
  "Write-Host '';" ^
  "Write-Host '   1. ARE THE FILES THERE?' -ForegroundColor Cyan;" ^
  "$okFiles=$true;" ^
  "foreach($p in @($ind,$str)){ if(Test-Path $p){ $f=Get-Item $p; Write-Host ('      YES  ' + $f.Name + '  (' + $f.Length + ' bytes)') -ForegroundColor Green } else { Write-Host ('      NO   ' + (Split-Path $p -Leaf) + ' is missing') -ForegroundColor Red; $okFiles=$false } };" ^
  "if(-not $okFiles){ Write-Host ''; Write-Host '   -> Run INSTALL NINJATRADER FILES.bat first.' -ForegroundColor Yellow; Write-Host ''; exit 1 };" ^
  "Write-Host '';" ^
  "Write-Host '   2. DID NINJATRADER COMPILE THEM?' -ForegroundColor Cyan;" ^
  "if(-not (Test-Path $dll)){ Write-Host '      NO   NinjaTrader has never compiled. Open it once.' -ForegroundColor Red; Write-Host ''; exit 1 };" ^
  "$d=(Get-Item $dll).LastWriteTime;" ^
  "$newest=(Get-Item $ind).LastWriteTime; if((Get-Item $str).LastWriteTime -gt $newest){$newest=(Get-Item $str).LastWriteTime};" ^
  "Write-Host ('      files last changed : ' + $newest);" ^
  "Write-Host ('      last compile       : ' + $d);" ^
  "$compiled = ($d -ge $newest.AddSeconds(-60));" ^
  "if($compiled){ Write-Host '      YES  compiled after the files were copied.' -ForegroundColor Green } else { Write-Host '      NO   the last compile predates the files.' -ForegroundColor Red; Write-Host '           Open NinjaTrader - it compiles on startup.' -ForegroundColor Yellow };" ^
  "Write-Host '';" ^
  "Write-Host '   3. ANY COMPILE ERRORS IN NINJATRADERS OWN LOG?' -ForegroundColor Cyan;" ^
  "$logdir=Join-Path $nt 'log';" ^
  "if(-not (Test-Path $logdir)){ Write-Host '      no log folder yet' } else {" ^
  "  $since=(Get-Date).AddDays(-2);" ^
  "  $logs=Get-ChildItem $logdir -Filter 'log.*.txt' | Where-Object { $_.LastWriteTime -gt $since };" ^
  "  $hits=@(); foreach($l in $logs){ $hits += (Select-String -Path $l.FullName -Pattern 'MarketSniper' -SimpleMatch -ErrorAction SilentlyContinue) };" ^
  "  if($hits.Count -eq 0){ if($compiled){ Write-Host '      nothing mentioning MarketSniper - it compiled clean.' -ForegroundColor Green } else { Write-Host '      nothing yet, because it has not been compiled. Check again after opening NinjaTrader.' -ForegroundColor Yellow } }" ^
  "  else { Write-Host ('      ' + $hits.Count + ' mention(s):') -ForegroundColor Yellow; $hits | Select-Object -Last 12 | ForEach-Object { Write-Host ('        ' + $_.Line.Trim()) } } };" ^
  "Write-Host '';" ^
  "Write-Host '   4. IS THE ATI SERVER ON? (needed for the Sniper to send orders)' -ForegroundColor Cyan;" ^
  "$inc=Join-Path $nt 'incoming';" ^
  "if(Test-Path $inc){ $n=@(Get-ChildItem $inc -Filter 'oif_*.txt' -ErrorAction SilentlyContinue).Count;" ^
  "  Write-Host ('      incoming folder exists. ' + $n + ' un-read order file(s) sitting in it.');" ^
  "  if($n -gt 0){ Write-Host '      ^^ files left behind mean ATI is OFF or the folder is wrong.' -ForegroundColor Red;" ^
  "    Write-Host '         Tools ^> Options ^> Automated Trading Interface ^> Enable ATI server' -ForegroundColor Yellow }" ^
  "  else { Write-Host '      nothing stuck - good.' -ForegroundColor Green } }" ^
  "else { Write-Host '      incoming folder does not exist yet - create it or connect once.' -ForegroundColor Yellow };" ^
  "Write-Host ''"

echo.
echo   ============================================================
echo     Send this whole window to Claude if anything is red.
echo   ============================================================
echo.
pause
