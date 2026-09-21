# ONCHAIN RADAR 봇만 골라서 끈다 (다른 파이썬·Claude Code 터미널은 건드리지 않는다).
# venv 의 pythonw.exe 는 런처라서, 실제 봇은 그 자식(Python313\pythonw.exe)으로 뜬다.
# 런처만 죽이면 자식이 살아남아 계속 폴링한다 — 그래서 런처의 자식까지 같이 끈다.
$all = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'"
$launchers = $all | Where-Object { $_.ExecutablePath -like '*onchain-radar*' -and $_.CommandLine -like '*main.py run*' }
$ids = @($launchers.ProcessId)
$ids += @($all | Where-Object { $ids -contains $_.ParentProcessId -and $_.CommandLine -like '*main.py run*' } | ForEach-Object { $_.ProcessId })
foreach ($p in ($ids | Select-Object -Unique)) {
  Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
  "종료: PID $p"
}
if (-not $ids) { "실행 중인 봇이 없습니다." }
