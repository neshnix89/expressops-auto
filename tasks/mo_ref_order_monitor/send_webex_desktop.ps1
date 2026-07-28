<#
Send one message to a Webex space by driving the Webex DESKTOP APP.

Used by tasks/mo_ref_order_monitor when the org blocks Webex bots and
integrations. Opens the space via its webexteams:// deep link, FORCES the
Webex window to the foreground, VERIFIES the foreground window really belongs
to Webex, and only then types.

The verification matters: SendKeys goes to whatever currently has focus, so
without it a failed activation types the message into the calling console (or
worse, into another app). On any failure this exits non-zero and types
nothing — the caller keeps the message queued and retries later.

Exit codes:
    0  sent
    2  Webex did not reach the foreground (nothing typed)
    3  no Webex window found (nothing typed)
    4  bad arguments

Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File send_webex_desktop.ps1 `
        -SpaceLink "webexteams://im?space=..." -Message "text" `
        -OpenDelay 6 -TypeDelay 1
#>
param(
    [Parameter(Mandatory = $true)][string]$SpaceLink,
    [Parameter(Mandatory = $true)][string]$Message,
    [double]$OpenDelay = 6,
    [double]$TypeDelay = 1
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SpaceLink) -or [string]::IsNullOrWhiteSpace($Message)) {
    Write-Error "SpaceLink and Message are required"; exit 4
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WxWin32 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr hWnd, out int pid);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
}
"@

function Get-WebexWindows {
    # Webex App is Webex.exe; older Teams client is CiscoCollabHost.exe.
    Get-Process |
        Where-Object {
            $_.MainWindowHandle -ne 0 -and
            ($_.ProcessName -match '^(Webex|CiscoCollabHost|webexmta)$' -or
             $_.MainWindowTitle -match 'Webex')
        }
}

# 1) Ask the OS to open the space in the Webex app.
Start-Process $SpaceLink | Out-Null
Start-Sleep -Seconds $OpenDelay

# 2) Locate the Webex window.
$wx = Get-WebexWindows | Select-Object -First 1
if (-not $wx) {
    Write-Error "No Webex window found - is the Webex desktop app running and signed in?"
    exit 3
}

# 3) Force it to the foreground (restore first if minimised).
if ([WxWin32]::IsIconic($wx.MainWindowHandle)) {
    [void][WxWin32]::ShowWindow($wx.MainWindowHandle, 9)  # SW_RESTORE
    Start-Sleep -Milliseconds 500
}
[void][WxWin32]::SetForegroundWindow($wx.MainWindowHandle)
Start-Sleep -Seconds $TypeDelay

# 4) VERIFY focus before typing anything. Windows can refuse a foreground
#    change (foreground lock), which is exactly how keystrokes leak elsewhere.
$fg = [WxWin32]::GetForegroundWindow()
$fgPid = 0
[void][WxWin32]::GetWindowThreadProcessId($fg, [ref]$fgPid)
$webexPids = @(Get-WebexWindows | Select-Object -ExpandProperty Id)
if ($webexPids -notcontains $fgPid) {
    $owner = try { (Get-Process -Id $fgPid -ErrorAction Stop).ProcessName } catch { "unknown" }
    Write-Error "Webex is not in the foreground (focus held by '$owner', pid $fgPid) - nothing typed"
    exit 2
}

# 5) Prime the compose box before the real text.
#    The first keystrokes after a window activation are routinely swallowed
#    while the app finishes settling (observed: the leading 5 characters of a
#    message went missing). Send a throwaway space, delete it, and only then
#    type the payload — so any dropped keystrokes cost us the primer, not the
#    start of the message. If the space was swallowed too, the backspace hits
#    an empty box and does nothing.
Start-Sleep -Milliseconds 800
[System.Windows.Forms.SendKeys]::SendWait(" ")
Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait("{BS}")
Start-Sleep -Milliseconds 400

# 6) Type the payload, then send with Enter.
[System.Windows.Forms.SendKeys]::SendWait($Message)
Start-Sleep -Milliseconds 500
[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
exit 0
