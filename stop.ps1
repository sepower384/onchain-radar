# ONCHAIN RADAR 봇 프로세스만 골라서 끈다 (다른 파이썬은 건드리지 않는다).
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*onchain-radar*main.py run*' -or ($_.CommandLine -like '*main.py run*' -and $_.ExecutablePath -like '*onchain-radar*') } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "종료: PID $($_.ProcessId)" }
