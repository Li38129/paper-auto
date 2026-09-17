param(
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30,
    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$At = "03:00",
    [string]$TaskName = "AutoPaper-Temp-Cleanup"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$cleanupScript = Join-Path $PSScriptRoot "prune-runtime.ps1"
$shellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath $cleanupScript -PathType Leaf)) {
    throw "找不到临时目录清理脚本：$cleanupScript"
}
if (-not (Test-Path -LiteralPath $shellPath -PathType Leaf)) {
    throw "找不到 Windows PowerShell：$shellPath"
}

$arguments = @(
    "-NoLogo"
    "-NoProfile"
    "-NonInteractive"
    "-ExecutionPolicy Bypass"
    "-File `"$cleanupScript`""
    "-RetentionDays $RetentionDays"
) -join " "

$action = New-ScheduledTaskAction -Execute $shellPath -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "定期清理 AutoPaper 浏览器缓存、临时构建产物与过期成功任务，保留失败任务和登录会话。" `
    -Force | Out-Null

Write-Host "已注册每周临时目录清理任务：$TaskName（每周日 $At）"
