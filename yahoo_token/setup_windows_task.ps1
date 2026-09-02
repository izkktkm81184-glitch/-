<#
.SYNOPSIS
    Yahoo!トークンの自動更新を Windows タスクスケジューラに登録する。

.DESCRIPTION
    毎日 1 回 watchdog を走らせる。残り日数がしきい値を超えていれば
    アクセストークンを温めるだけで終了し、しきい値を切っていれば
    再認可（ブラウザ自動操作）まで自動で行う。

    管理者権限は不要（ログオンユーザーのタスクとして登録する）。
    ※ 再認可でブラウザを動かすため「ユーザーがログオンしているときのみ実行」
      が既定。PC を付けっぱなしにできない場合は -RunWhetherLoggedOn を付けて
      登録し、パスワード保存の確認に応じること。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup_windows_task.ps1
    powershell -ExecutionPolicy Bypass -File .\setup_windows_task.ps1 -At 08:30 -ThresholdDays 10
    powershell -ExecutionPolicy Bypass -File .\setup_windows_task.ps1 -Unregister
#>
param(
    [string]$TaskName = "YahooTokenWatchdog",
    [string]$At = "09:00",
    [double]$ThresholdDays = 7,
    [ValidateSet("auto", "assist", "manual")][string]$Mode = "auto",
    [string]$PythonExe = "",
    [switch]$RunWhetherLoggedOn,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "タスク '$TaskName' を削除しました。"
    return
}

if (-not $PythonExe) {
    $found = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $found) { $found = Get-Command py.exe -ErrorAction SilentlyContinue }
    if (-not $found) { throw "python が見つかりません。-PythonExe でフルパスを指定してください。" }
    $PythonExe = $found.Source
}

$watchdog = Join-Path $scriptDir "yahoo_token.py"
if (-not (Test-Path $watchdog)) { throw "yahoo_token.py が見つかりません: $watchdog" }

$logDir = Join-Path $scriptDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# ログ出力は Python 側 (--log-dir) が行う。cmd.exe を挟まないのでクォート事故がない。
$arguments = "`"$watchdog`" watchdog --threshold-days $ThresholdDays --mode $Mode --log-dir `"$logDir`""
$action    = New-ScheduledTaskAction -Execute $PythonExe -Argument $arguments -WorkingDirectory $scriptDir

$trigger  = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)

if ($RunWhetherLoggedOn) {
    # 画面が無くても走る。ヘッドレスなので再認可も動くが、初回の assist は
    # 対話ログオン中に済ませておくこと。
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Password -RunLevel Limited
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description "Yahoo!ショッピングAPI のトークンを毎日点検し、期限が近ければ自動で再認可する" | Out-Null

Write-Host "タスク '$TaskName' を登録しました。"
Write-Host "  実行時刻   : 毎日 $At"
Write-Host "  しきい値   : 残り $ThresholdDays 日を切ったら再認可"
Write-Host "  再認可方式 : $Mode"
Write-Host "  ログ       : $logDir"
Write-Host ""
Write-Host "動作確認: Start-ScheduledTask -TaskName $TaskName"
