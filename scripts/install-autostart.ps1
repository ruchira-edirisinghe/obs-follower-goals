# Starts the obs-follower-goals helper automatically when you sign in to Windows (no console window).
# Usage (from the repo folder):   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
# Remove it again with:           powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1
$ErrorActionPreference = "Stop"
$server = (Resolve-Path (Join-Path $PSScriptRoot "..\server.py")).Path

# pythonw/pyw runs without a console window
$pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source }
if (-not $pyw) { throw "Python not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')." }

$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "OBS Follower Goals.lnk"
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = "`"$server`""
$s.WorkingDirectory = Split-Path $server
$s.Description = "obs-follower-goals helper (http://127.0.0.1:8765/counts)"
$s.Save()
Write-Host "Autostart shortcut created: $lnk"

# start it now too, unless it is already running
try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8765/health | Out-Null; Write-Host "Helper is already running." }
catch { Start-Process $pyw -ArgumentList "`"$server`"" -WorkingDirectory (Split-Path $server); Write-Host "Helper started: http://127.0.0.1:8765/counts" }
