<#
.SYNOPSIS
    Register the MO Ref-order-no monitor scheduled task with laptop-safe settings.

.DESCRIPTION
    Replaces the schtasks.exe registration, which cannot set the two options
    that silently kill runs on a laptop:

      * AllowStartIfOnBatteries / DontStopIfGoingOnBatteries
          Task Scheduler REFUSES to start a task on battery power by default,
          so every slot is skipped while unplugged — with no error anywhere.
      * StartWhenAvailable
          Slots missed while the machine was asleep or logged off are lost by
          default. With this on, one missed run fires shortly after you log in.

    Also sets MultipleInstances=IgnoreNew so a slow run can never overlap the
    next slot, and a 20-minute execution limit so a hung run cannot block the
    schedule indefinitely.

    The task runs INTERACTIVELY as the current user — required, because the
    Webex desktop transport types into the app and needs a logged-on session.

.EXAMPLE
    # primary laptop: 08:00-17:00 every 30 min
    powershell -ExecutionPolicy Bypass -File .\scripts\setup_mo_ref_order_schedule.ps1

.EXAMPLE
    # second laptop: 09:15 start, offset from the primary's :00/:30 slots
    .\scripts\setup_mo_ref_order_schedule.ps1 -StartTime 09:15 -DurationHours 7.75 -Runner run_mo_ref_order_monitor_portable.bat

.EXAMPLE
    # inspect what is registered / when it last ran
    .\scripts\setup_mo_ref_order_schedule.ps1 -ShowOnly
#>
[CmdletBinding()]
param(
    [string]$TaskName        = "MO_RefOrder_Monitor",
    [string]$StartTime       = "08:00",
    [double]$DurationHours   = 9,
    [int]$IntervalMinutes    = 30,
    [string]$Runner          = "run_mo_ref_order_monitor.bat",
    [switch]$ShowOnly
)

$ErrorActionPreference = "Stop"
function Say($m) { Write-Host "[schedule] $m" }

# Repo root = parent of the scripts folder this file lives in.
$repoRoot = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $repoRoot $Runner

if ($ShowOnly) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Say "task '$TaskName' is NOT registered."; exit 0 }
    $i = Get-ScheduledTaskInfo -TaskName $TaskName
    Say "task     : $TaskName ($($t.State))"
    Say "action   : $($t.Actions.Execute)"
    Say "last run : $($i.LastRunTime)  result=$($i.LastTaskResult)"
    Say "next run : $($i.NextRunTime)"
    Say "battery-safe    : $(-not $t.Settings.DisallowStartIfOnBatteries)"
    Say "catch-up missed : $($t.Settings.StartWhenAvailable)"
    exit 0
}

if (-not (Test-Path $bat)) { Write-Host "[schedule] ERROR: runner not found: $bat" -ForegroundColor Red; exit 1 }
Say "runner: $bat"

$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $repoRoot

# A daily trigger carrying a repetition block: fires at $StartTime, then every
# $IntervalMinutes for $DurationHours (i.e. 08:00-17:00).
$trigger = New-ScheduledTaskTrigger -Daily -At $StartTime
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $StartTime `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Hours $DurationHours)).Repetition

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

# Interactive: the Webex desktop transport must type into a logged-on session.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Say "registered: every $IntervalMinutes min from $StartTime for $DurationHours h"
Say "  runs on battery : yes"
Say "  catches up missed slots : yes"
Say "  overlapping runs : ignored"
Say "  next run : $($info.NextRunTime)"
Write-Host ""
Say "Verify any time with:  .\scripts\setup_mo_ref_order_schedule.ps1 -ShowOnly"
Say "Run once now with   :  Start-ScheduledTask -TaskName '$TaskName'"
