<#
Send one message to a Webex space by driving the Webex DESKTOP APP.

Used by tasks/mo_ref_order_monitor when the org blocks Webex bots and
integrations. Opens the space via its webexteams:// deep link, picks the real
CHAT window, forces it to the foreground, PASTES the message, READS THE COMPOSE
BOX BACK to confirm the text is really there, and only then presses Enter.

Failure modes this guards against, all observed in production:
  * keystrokes leaking into whatever else had focus (typed into the console),
  * an auxiliary Webex window (e.g. an image preview) being targeted instead of
    the chat window — it shares the process ID, so a PID-level check passes
    while the message goes nowhere. Verification is by window HANDLE.
  * THE SILENT ONE: the space is still switching after the deep link, Webex
    discards the compose contents mid-render, and a blind Enter posts nothing.
    Focus was held, so the old code reported success. Two alerts were lost this
    way on 13-Aug — one of them a RESOLVED notice a colleague waited an hour
    for. The read-back is what closes this hole; the paste is retried up to 3
    times first, since the usual cause is simply that the space needed longer.

On any failure this exits non-zero WITHOUT sending; the caller keeps the
message queued and retries next run.

Exit codes:
    0  sent (and verified present in the compose box beforehand)
    2  the chat window did not reach the foreground (nothing sent)
    3  no usable Webex chat window found (nothing sent)
    4  bad arguments
    6  compose box never held the message after 3 paste attempts (nothing sent)
    7  the clipboard is not usable from this session (nothing sent)
    8  the workstation is locked (nothing sent; expected out of hours)

Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File send_webex_desktop.ps1 `
        -SpaceLink "webexteams://im?space=..." -MessageFile msg.txt `
        -OpenDelay 6 -TypeDelay 2

Add -AllowUnverified by hand to send even when the read-back cannot confirm.
It is a DIAGNOSTIC: if the message arrives with it and not without it, the
paste works and it is the read-back this machine will not answer.
#>
param(
    [Parameter(Mandatory = $true)][string]$SpaceLink,
    # The message text, or -MessageFile holding it (UTF-8, real line breaks).
    # It is ONE post: pasted whole, verified, then sent with a single Enter.
    [string]$Message,
    [string]$MessageFile,
    [double]$OpenDelay = 6,
    [double]$TypeDelay = 2,
    # DIAGNOSTIC ONLY, never set by the poller. Sends even when the read-back
    # could not confirm the text. Use it to tell the two failure halves apart:
    # if the message arrives WITH this flag but not without it, the paste is
    # fine and it is the read-back (Ctrl+A/Ctrl+C) that this machine will not
    # answer. Pressing Enter on an empty compose box posts nothing, so this is
    # safe to try — what it is not is honest, which is why the poller never
    # uses it.
    [switch]$AllowUnverified
)

$ErrorActionPreference = "Stop"

# Write-Error is TERMINATING under ErrorActionPreference=Stop: it kills the
# script and PowerShell exits 1, so every `exit <code>` after one was dead
# code and the caller saw 1 for everything. Observed 13-Aug: a refusal to send
# (6) was reported as a transient failure and retried. Write to stderr by hand.
$script:prevClip = $null
function Fail([string]$msg, [int]$code) {
    if ($script:prevClip) { try { Set-Clipboard -Value $script:prevClip } catch { } }
    [Console]::Error.WriteLine($msg)
    exit $code
}

if ([string]::IsNullOrWhiteSpace($SpaceLink)) {
    Fail "SpaceLink is required" 4
}
$Payload = ""
if ($MessageFile) {
    if (-not (Test-Path $MessageFile)) { Fail "MessageFile not found: $MessageFile" 4 }
    $Payload = Get-Content -Path $MessageFile -Encoding UTF8 -Raw
} elseif (-not [string]::IsNullOrWhiteSpace($Message)) {
    $Payload = $Message
}
$Payload = $Payload.TrimEnd("`r", "`n")
if ([string]::IsNullOrWhiteSpace($Payload)) { Fail "No message to send" 4 }

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

# 0) A LOCKED workstation cannot be typed into at all. Detect it up front and
#    say so, rather than opening the space, failing the focus check, and
#    reporting "focus held by ''" — which reads like a bug instead of a laptop
#    sitting locked at 18:31. LogonUI.exe is present exactly while the lock or
#    sign-in screen is up.
if (Get-Process LogonUI -ErrorAction SilentlyContinue) {
    Fail "workstation is LOCKED - nothing sent; the alert stays queued for the first run after unlock" 8
}

# 1) Ask the OS to open the space in the Webex app.
Start-Process $SpaceLink | Out-Null
Start-Sleep -Seconds $OpenDelay

# 2) Locate the CHAT window specifically.
$win = Get-WebexChatWindow
if (-not $win) {
    Fail "No Webex chat window found - is the app running, signed in, and not showing only a preview window?" 3
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
    Fail ("Target chat window is not in the foreground (focus held by '{0}') - nothing typed" -f $fgTitle) 2
}

# 5-7) Paste, READ THE COMPOSE BOX BACK, and only send once it matches.
#      Retried in-process: the failure this guards against is a space that is
#      still switching, which a few more seconds usually fixes. Giving up here
#      would push the alert to the next poll — 30 minutes late for something
#      like a RESOLVED notice.
try { $script:prevClip = Get-Clipboard -Raw -ErrorAction SilentlyContinue } catch { }

function Normalize([string]$t) { if ($null -eq $t) { return "" } ($t -replace '\s+', ' ').Trim() }
$sentinel = "__EO_CLIPBOARD_SENTINEL__"
$wantN = Normalize $Payload

# Prove the clipboard itself round-trips BEFORE blaming Webex. Verification is
# built on Set-Clipboard/Get-Clipboard; if those are broken (clipboard locked
# by another app, no window station in a non-interactive session) then every
# read-back returns empty and the message looks lost when it never left here.
$clipOk = $false
try {
    Set-Clipboard -Value "__EO_CLIP_TEST__"
    Start-Sleep -Milliseconds 250
    $clipOk = ((Normalize (Get-Clipboard -Raw -ErrorAction Stop)) -eq "__EO_CLIP_TEST__")
} catch { }
Write-Host "[webex-ps] clipboard round-trip: $clipOk"
if (-not $clipOk) {
    Fail "clipboard is not usable from this session - cannot paste or verify. Nothing sent." 7
}

$sent = $false
$lastSeen = ""
foreach ($attempt in 1..3) {
    if ($attempt -gt 1) {
        # Clear whatever partially landed, then let the space settle longer.
        [System.Windows.Forms.SendKeys]::SendWait("^a")
        Start-Sleep -Milliseconds 200
        [System.Windows.Forms.SendKeys]::SendWait("{BACKSPACE}")
        Start-Sleep -Seconds ($attempt * 2)

        # Focus can be lost between attempts (a toast, a screensaver nudge).
        [void][WxWin32]::SetForegroundWindow($win.Handle)
        Start-Sleep -Milliseconds 500
    }

    $fgNow = [WxWin32]::GetForegroundWindow()
    if ($fgNow -ne $win.Handle) {
        # Say so rather than silently burning an attempt — "why did all three
        # fail?" is unanswerable without this line.
        Write-Host ("[webex-ps] attempt {0}: SKIPPED - focus lost to '{1}'" -f $attempt, [WxWin32]::Title($fgNow))
        continue
    }

    # Prime the compose box. The first keystroke after a window is activated is
    # routinely swallowed by Webex (this is why typing used to lose its first
    # few characters), and if that keystroke is the whole Ctrl+V then nothing
    # is pasted at all — which looks identical to a still-switching space.
    [System.Windows.Forms.SendKeys]::SendWait(" ")
    Start-Sleep -Milliseconds 300
    [System.Windows.Forms.SendKeys]::SendWait("{BACKSPACE}")
    Start-Sleep -Milliseconds 300

    # Paste. Pasting beats typing: instant (no per-character race with the
    # app), and immune to SendKeys escaping — emoji and punctuation go through
    # verbatim.
    Set-Clipboard -Value $Payload
    Start-Sleep -Milliseconds 500
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds 1200

    # Read the compose box back. A sentinel goes on the clipboard first, so a
    # Ctrl+C that copies nothing cannot leave our own payload behind and fake a
    # match.
    Set-Clipboard -Value $sentinel
    Start-Sleep -Milliseconds 250
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 400
    [System.Windows.Forms.SendKeys]::SendWait("^c")
    Start-Sleep -Milliseconds 800

    $got = ""
    try { $got = Get-Clipboard -Raw -ErrorAction SilentlyContinue } catch { }
    $gotN = Normalize $got
    $wasSentinel = ($gotN -eq (Normalize $sentinel))
    if ($wasSentinel) { $gotN = "" }
    $lastSeen = $gotN

    $why = "MISMATCH"
    if ($wasSentinel) { $why = "EMPTY (Ctrl+C copied nothing)" }
    elseif ($gotN -eq $wantN) { $why = "MATCH" }
    $peek = $gotN
    if ($peek.Length -gt 60) { $peek = $peek.Substring(0, 60) + "..." }
    Write-Host ("[webex-ps] attempt {0}: want {1} chars, got {2} - {3} '{4}'" -f $attempt, $wantN.Length, $gotN.Length, $why, $peek)

    if ($gotN -ne $wantN) { continue }

    # Verified. Deselect (Ctrl+A left everything selected) and send.
    [System.Windows.Forms.SendKeys]::SendWait("{END}")
    Start-Sleep -Milliseconds 250
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Start-Sleep -Milliseconds 400
    $sent = $true
    break
}

if ($script:prevClip) { try { Set-Clipboard -Value $script:prevClip } catch { } }

if (-not $sent) {
    $preview = if ([string]::IsNullOrWhiteSpace($lastSeen)) {
        "<empty - the paste did not land>"
    } elseif ($lastSeen.Length -gt 120) { $lastSeen.Substring(0, 120) + "..." }
    else { $lastSeen }

    if ($AllowUnverified) {
        Write-Host "[webex-ps] -AllowUnverified: pressing Enter without confirmation"
        [System.Windows.Forms.SendKeys]::SendWait("{END}")
        Start-Sleep -Milliseconds 250
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        Start-Sleep -Milliseconds 400
        Write-Host "SENT 1 (UNVERIFIED - check the space yourself)"
        exit 0
    }

    Fail "compose box never held the message after 3 attempts - nothing sent. Found: '$preview'" 6
}

Write-Host "SENT 1"
exit 0
