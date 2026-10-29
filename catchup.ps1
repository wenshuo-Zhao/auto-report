# 补跑：如果今天的简报还没生成，就跑一次
$ErrorActionPreference = "Continue"
$base = "D:\工作\code\auto-report"
$today = Get-Date -Format "yyyy-MM-dd"
$file = Join-Path $base "output\情报-$today.md"

if (Test-Path $file) {
    # 已经生成过，什么都不做
    exit 0
}

$py = Join-Path $base ".venv\Scripts\python.exe"
$script = Join-Path $base "report.py"
Set-Location $base
& $py $script *>> (Join-Path $base "logs\catchup.log")
exit $LASTEXITCODE
