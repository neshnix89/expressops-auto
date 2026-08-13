<#
.SYNOPSIS
    Register ANY of this repo's runners as a laptop-safe scheduled task.

.DESCRIPTION
    Generalises setup_mo_ref_order_schedule.ps1 so every task gets the same
    treatment. schtasks.exe cannot set the two options that silently kill runs
    on a laptop:

      * AllowStartIfOnBatteries / DontStopIfGoingOnBatteries
          Task Scheduler REFUSES to start a task on battery by default, so
          slots are skipped while unplugged — with no error anywhere.
      * StartWhenAvailable
          Slots missed while asleep or logged off are lost by default.

    Two trigger shapes:
      * -AtTimes "09:30","12:45","16:00"   one run at each time (daily)
      * -AtTimes "08:00" -IntervalMinutes 30 -DurationHours 9
                                           every 30 min from 08:00 to 17:00

    -Disable / -Enable flip a task without deleting it — that is the handover
    switch when one machine takes over from another. Tasks that WRITE (JIRA
    comments, Confluence pages) keep their state locally, so two machines
    running the same task will duplicate work. Only one at a time.

.EXAMPLE
    # MR status report, once a day
    .\scripts\setup_schedule.ps1 -TaskName MR_Status_Report -Runner scheduled_mr_publish.bat -AtTimes "10:00"

.EXAMPLE
    # Costing/HS trigger, three times a day
    .\scripts\setup_schedule.ps1 -TaskName CostingHSCode -Runner run_costing_hs_code_trigger.bat -AtTimes "09:30","12:45","16:00"

.EXAMPLE
    # hand over to the other laptop: turn mine off
    .\scripts\setup_schedule.ps1 -TaskName MR_Status_Report -Disable
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$TaskName,
    [string]$Runner,
    [string[]]$AtTimes = @("09:00"),
    # 0 = fire once at each -AtTimes entry. >0 = repeat from the FIRST time.
    [int]$IntervalMinutes = 0,
    [double]$DurationHours = 9,
    [int]$TimeLimitMinutes = 20,
    [switch]$ShowOnly,
    [switch]$Disable,
    [switch]$Enable
)

$ErrorActionPreference = "Stop"
function Say($m) { Write-Host "[schedule] $m" }
function Die($m) { Write-Host "[schedule] ERROR: $m" -ForegroundColor Red; exit 1 }

$repoRoot = Split-Path -Parent $PSScriptRoot

function Show($name) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { Say "task '$name' is NOT registered."; return }
    $i = Get-ScheduledTaskInfo -TaskName $name
    Say "task     : $name ($($t.State))"
    Say "action   : $($t.Actions.Execute)"
    Say "last run : $($i.LastRunTime)  result=$($i.LastTaskResult)"
    Say "next run : $($i.NextRunTime)"
    Say "battery-safe    : $(-not $t.Settings.DisallowStartIfOnBatteries)"
    Say "catch-up missed : $($t.Settings.StartWhenAvailable)"
}

if ($ShowOnly) { Show $TaskName; exit 0 }

if ($Disable -or $Enable) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Die "task '$TaskName' is not registered on this machine." }
    if ($Disable) { Disable-ScheduledTask -TaskName $TaskName | Out-Null; Say "DISABLED '$TaskName' (still registered)" }
    else { Enable-ScheduledTask -TaskName $TaskName | Out-Null; Say "ENABLED '$TaskName'" }
    Show $TaskName
    exit 0
}

if (-not $Runner) { Die "-Runner is required when registering (e.g. run_mr_report.bat)" }
$bat = Join-Path $repoRoot $Runner
if (-not (Test-Path $bat)) { Die "runner not found: $bat" }
Say "runner: $bat"

$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $repoRoot

if ($IntervalMinutes -gt 0) {
    # One daily trigger carrying a repetition block.
    $trigger = New-ScheduledTaskTrigger -Daily -At $AtTimes[0]
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $AtTimes[0] `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Hours $DurationHours)).Repetition
    $triggers = @($trigger)
    $desc = "every $IntervalMinutes min from $($AtTimes[0]) for $DurationHours h"
} else {
    # One plain daily trigger per requested time.
    $triggers = @($AtTimes | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
    $desc = "daily at " + ($AtTimes -join ", ")
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $TimeLimitMinutes)

# Interactive: the Webex desktop transport types into a logged-on session, and
# the other tasks are equally happy running as the signed-in user.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force | Out-Null

Say "registered '$TaskName': $desc"
Say "  runs on battery : yes"
Say "  catches up missed slots : yes"
Say "  overlapping runs : ignored"
Show $TaskName
Write-Host ""
Say "Run once now  :  Start-ScheduledTask -TaskName '$TaskName'"
Say "Hand over     :  .\scripts\setup_schedule.ps1 -TaskName '$TaskName' -Disable"
