<#
.SYNOPSIS
Registers (or removes) a Windows Task Scheduler job that runs the listing
agent on a repeating interval.

.DESCRIPTION
The scheduled action runs the project's own venv Python so the task does not
depend on whatever Python happens to be on PATH. Output is appended to
data\task.log because Task Scheduler swallows stdout; without the redirect a
failing run would be invisible.

.EXAMPLE
.\scripts\register-task.ps1                 # every 30 minutes
.\scripts\register-task.ps1 -EveryMinutes 60
.\scripts\register-task.ps1 -Unregister
#>
param(
    [switch]$Unregister,
    [int]$EveryMinutes = 30,
    [string]$TaskName = "listing-agent"
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
    return
}

# Resolve everything relative to the script so it works from any CWD.
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDir = Join-Path $projectRoot "data"
$log = Join-Path $logDir "task.log"

if (-not (Test-Path $python)) {
    throw "venv Python not found at $python. Run 'python -m venv .venv' and 'pip install -e .' first."
}
if (-not (Test-Path (Join-Path $projectRoot "criteria.yaml"))) {
    throw "criteria.yaml not found in $projectRoot. Copy criteria.example.yaml and edit it first."
}
New-Item -ItemType Directory -Force $logDir | Out-Null

# powershell.exe wrapper exists only for the log redirect (*>> captures both
# stdout and stderr). Task Scheduler itself keeps no output.
$command = "& '$python' -m listing_agent *>> '$log'"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$command`"" `
    -WorkingDirectory $projectRoot

# A -Once trigger with a repetition interval is the documented way to get
# "every N minutes forever"; daily triggers cap repetition at 24h.
$trigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

# StartWhenAvailable: if the machine was asleep at trigger time, run on wake
# instead of silently skipping. The store's dedup makes late runs harmless.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Polls real estate listings and notifies on matches. Project: $projectRoot" `
    -Force | Out-Null

Write-Host "Registered '$TaskName' to run every $EveryMinutes minutes."
Write-Host "Logs: $log"
Write-Host "Remove with: .\scripts\register-task.ps1 -Unregister"
