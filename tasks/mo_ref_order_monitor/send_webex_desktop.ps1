<#
Send one message to a Webex space by driving the Webex DESKTOP APP.

Used by tasks/mo_ref_order_monitor when the org blocks Webex bots and
integrations. Opens the space via its webexteams:// deep link, picks the real
CHAT window, forces it to the foreground, VERIFIES that exact window has focus,
and only then types.

Two failure modes this guards against, both observed in testing:
  * keystrokes leaking into whatever else had focus (typed into the console),
  * an auxiliary Webex window (e.g. an image preview) being targeted instead of
    the chat window — it shares the process ID, so a PID-level check passes
    while the message goes nowhere useful. Verification is therefore by window
    HANDLE, not by process.

On any failure this exits non-zero having typed nothing; the caller keeps the
message queued and retries later.

Exit codes:
    0  sent
    2  the chat window did not reach the foreground (nothing typed)
    3  no usable Webex chat window found (nothing typed)
    4  bad arguments

Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File send_webex_desktop.ps1 `
        -SpaceLink "webexteams://im?space=..." -Message "text" `
        -OpenDelay 6 -TypeDelay 2
#>
param(
    [Parameter(Mandatory = $true)][string]$SpaceLink,
    # One message, or -MessageFile for several (one per line, already escaped).
    # Batching matters: re-opening the space deep-link per message re-renders
    # the compose box, and messages typed into a mid-render box are lost.
    [string]$Message,
    [string]$MessageFile,
    [double]$OpenDelay = 6,
    [double]$TypeDelay = 2,
    [double]$SendGap = 2
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SpaceLink)) {
    Write-Error "SpaceLink is required"; exit 4
}
$messages = @()
if ($MessageFile) {
    if (-not (Test-Path $MessageFile)) { Write-Error "MessageFile not found: $MessageFile"; exit 4 }
    $messages = @(Get-Content -Path $MessageFile -Encoding UTF8 | Where-Object { $_.Trim() })
} elseif (-not [string]::IsNullOrWhiteSpace($Message)) {
    $messages = @($Message)
}
if (-not $messages.Count) { Write-Error "No message(s) to send"; exit 4 }

Add-Type -AssemblyName System.Windows.Forms

# Compile the Win32 helper only once per session. Re-running Add-Type for an
# existing type raises, and the compile step itself is the flakiest part of
# this script (observed: a transient "error occurred while creating the
# pipeline" that succeeded on the next attempt).
if (-not ('WxWin32' -as [type])) {
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class WxWin32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder s, int max);
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr hWnd, out int pid);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    public struct RECT { public int Left, Top, Right, Bottom; }

    public static List<IntPtr> VisibleWindows() {
        List<IntPtr> list = new List<IntPtr>();
        EnumWindows(delegate(IntPtr h, IntPtr l) {
            if (IsWindowVisible(h)) list.Add(h);
            return true;
        }, IntPtr.Zero);
        return list;
    }
    public static string Title(IntPtr h) {
        int len = GetWindowTextLength(h);
        if (len == 0) return "";
        StringBuilder sb = new StringBuilder(len + 1);
        GetWindowText(h, sb, sb.Capacity);
        return sb.ToString();
    }
    public static int Pid(IntPtr h) { int p; GetWindowThreadProcessId(h, out p); return p; }
    public static long Area(IntPtr h) {
        RECT r; if (!GetWindowRect(h, out r)) return 0;
        return (long)(r.Right - r.Left) * (long)(r.Bottom - r.Top);
    }
}
"@
}

# Titles that indicate a preview / viewer window rather than the chat window.
$previewPattern = '\.(png|jpe?g|gif|bmp|webp|heic|pdf|docx?|xlsx?|pptx?|mp4|mov)$|^Image$|Preview'

function Get-WebexChatWindow {
    $procIds = @(Get-Process |
        Where-Object { $_.ProcessName -match '^(Webex|CiscoCollabHost|webexmta)$' } |
        Select-Object -ExpandProperty Id)
    if (-not $procIds) { return $null }

    $cands = foreach ($h in [WxWin32]::VisibleWindows()) {
        $t = [WxWin32]::Title($h)
        if ([string]::IsNullOrWhiteSpace($t)) { continue }
        $wpid = [WxWin32]::Pid($h)
        if ($procIds -notcontains $wpid) { continue }
        [pscustomobject]@{
            Handle    = $h
            Title     = $t
            Pid       = $wpid
            Area      = [WxWin32]::Area($h)
            IsPreview = ($t -match $previewPattern)
        }
    }
    if (-not $cands) { return $null }

    Write-Host "[webex-ps] candidate windows:"
    foreach ($c in $cands) {
        Write-Host ("    '{0}' pid={1} area={2} preview={3}" -f $c.Title, $c.Pid, $c.Area, $c.IsPreview)
    }

    # Prefer a non-preview window whose title mentions Webex; then largest area
    # (the chat window is the big one, previews and toasts are small).
    $ranked = $cands |
        Where-Object { -not $_.IsPreview } |
        Sort-Object @{ Expression = { if ($_.Title -match 'Webex') { 0 } else { 1 } } },
                    @{ Expression = { $_.Area }; Descending = $true }
    if (-not $ranked) { return $null }
    return @($ranked)[0]
}

# 1) Ask the OS to open the space in the Webex app.
Start-Process $SpaceLink | Out-Null
Start-Sleep -Seconds $OpenDelay

# 2) Locate the CHAT window specifically.
$win = Get-WebexChatWindow
if (-not $win) {
    Write-Error "No Webex chat window found - is the app running, signed in, and not showing only a preview window?"
    exit 3
}
Write-Host ("[webex-ps] target: '{0}' (handle {1})" -f $win.Title, $win.Handle)

# 3) Force it to the foreground (restore first if minimised).
if ([WxWin32]::IsIconic($win.Handle)) {
    [void][WxWin32]::ShowWindow($win.Handle, 9)  # SW_RESTORE
    Start-Sleep -Milliseconds 500
}
[void][WxWin32]::SetForegroundWindow($win.Handle)
Start-Sleep -Seconds $TypeDelay

# 4) VERIFY by HANDLE. A PID check is not enough: an image-preview window
#    shares the chat window's process, so it would pass while swallowing the
#    message.
$fg = [WxWin32]::GetForegroundWindow()
if ($fg -ne $win.Handle) {
    $fgTitle = [WxWin32]::Title($fg)
    Write-Error ("Target chat window is not in the foreground (focus held by '{0}') - nothing typed" -f $fgTitle)
    exit 2
}

# 5) Prime the compose box. The first keystrokes after activation are routinely
#    swallowed while the app settles (observed: leading 5 chars lost). Spend a
#    throwaway space + backspace so any loss costs the primer, not the message.
Start-Sleep -Milliseconds 800
[System.Windows.Forms.SendKeys]::SendWait(" ")
Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait("{BS}")
Start-Sleep -Milliseconds 400

# 6) Type each payload, Enter after each. The space is opened ONCE above, so
#    the compose box is not re-rendered between messages.
#    "SENT n" on stdout lets the caller dequeue exactly what got through, even
#    if a later message fails.
$i = 0
foreach ($m in $messages) {
    $i++
    [System.Windows.Forms.SendKeys]::SendWait($m)
    Start-Sleep -Milliseconds 500
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Write-Host "SENT $i"
    if ($i -lt $messages.Count) { Start-Sleep -Seconds $SendGap }
}
exit 0
