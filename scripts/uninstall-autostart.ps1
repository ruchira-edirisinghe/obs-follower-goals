# Removes the obs-follower-goals autostart shortcut and stops a running helper.
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "OBS Follower Goals.lnk"
if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Autostart removed." } else { Write-Host "No autostart shortcut found." }
Get-CimInstance Win32_Process -Filter "Name like 'pythonw%.exe' or Name like 'pyw%.exe'" |
  Where-Object { $_.CommandLine -like "*server.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Stopped helper (PID $($_.ProcessId))." }
